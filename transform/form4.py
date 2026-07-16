"""Parse a Form 3/4/5 ownership-document XML into typed rows.

SEC Form 4 filings carry a clean, schema-versioned XML (`<ownershipDocument>`),
so this is pure `xml.etree` — no HTML scraping. The parser is deliberately
tolerant: any missing element yields None rather than raising, because 30+ years
of filings span several schema versions and optional fields come and go.

A filing reports one (occasionally several) *reporting owners* and a list of
non-derivative and derivative *transactions*. We attribute transactions to the
filing's primary (first) reporting owner; multi-owner filings are rare and are
flagged by the caller via `len(owners)`.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Owner:
    cik: int | None
    name: str | None
    is_director: bool
    is_officer: bool
    is_ten_pct_owner: bool
    is_other: bool
    officer_title: str | None


@dataclass
class Transaction:
    is_derivative: bool
    security_title: str | None
    transaction_date: dt.date | None
    transaction_code: str | None
    shares: float | None
    price_per_share: float | None
    acquired_disposed: str | None
    shares_owned_following: float | None
    direct_indirect: str | None


@dataclass
class Form4:
    document_type: str | None
    period_of_report: dt.date | None
    issuer_cik: int | None
    issuer_name: str | None
    issuer_symbol: str | None
    owners: list[Owner]
    transactions: list[Transaction]


# --- small tolerant extractors --------------------------------------------
def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.findtext(path)
    if found is None:
        return None
    found = found.strip()
    return found or None


def _val(el: ET.Element | None, path: str) -> str | None:
    """Many Form 4 fields wrap their content in a <value> child."""
    return _text(el, f"{path}/value")


def _int(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        # Some filers write comma-grouped share counts ("1,000").
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _date(s: str | None) -> dt.date | None:
    if not s:
        return None
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _bool(s: str | None) -> bool:
    """SEC uses '1'/'0' and (rarely) 'true'/'false'."""
    return (s or "").strip().lower() in {"1", "true"}


def _owner(o: ET.Element) -> Owner:
    rel = o.find("reportingOwnerRelationship")
    return Owner(
        cik=_int(_text(o, "reportingOwnerId/rptOwnerCik")),
        name=_text(o, "reportingOwnerId/rptOwnerName"),
        is_director=_bool(_text(rel, "isDirector")),
        is_officer=_bool(_text(rel, "isOfficer")),
        is_ten_pct_owner=_bool(_text(rel, "isTenPercentOwner")),
        is_other=_bool(_text(rel, "isOther")),
        officer_title=_text(rel, "officerTitle"),
    )


def _transaction(t: ET.Element, *, is_derivative: bool) -> Transaction:
    return Transaction(
        is_derivative=is_derivative,
        security_title=_val(t, "securityTitle"),
        transaction_date=_date(_val(t, "transactionDate")),
        transaction_code=_text(t, "transactionCoding/transactionCode"),
        shares=_float(_val(t, "transactionAmounts/transactionShares")),
        price_per_share=_float(_val(t, "transactionAmounts/transactionPricePerShare")),
        acquired_disposed=_val(t, "transactionAmounts/transactionAcquiredDisposedCode"),
        shares_owned_following=_float(
            _val(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
        direct_indirect=_val(t, "ownershipNature/directOrIndirectOwnership"),
    )


def _extract_ownership_xml(raw: bytes) -> bytes:
    """Return just the <ownershipDocument> element.

    The clean doc.xml passes through unchanged, but the full-submission .txt (our
    fallback when no primary document URL was recorded) wraps the same XML in an
    SGML envelope. Slicing to the element makes both parse identically and also
    drops any leading <?xml?> declaration or stray bytes."""
    start = raw.find(b"<ownershipDocument")
    end = raw.rfind(b"</ownershipDocument>")
    if start == -1 or end == -1:
        raise ValueError("no <ownershipDocument> element found")
    return raw[start:end + len(b"</ownershipDocument>")]


def parse(xml_bytes: bytes) -> Form4:
    """Parse ownership-document XML. Raises on non-XML input (caller isolates)."""
    root = ET.fromstring(_extract_ownership_xml(xml_bytes))

    issuer = root.find("issuer")
    owners = [_owner(o) for o in root.findall("reportingOwner")]

    transactions: list[Transaction] = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        transactions.append(_transaction(t, is_derivative=False))
    for t in root.findall("derivativeTable/derivativeTransaction"):
        transactions.append(_transaction(t, is_derivative=True))

    return Form4(
        document_type=_text(root, "documentType"),
        period_of_report=_date(_text(root, "periodOfReport")),
        issuer_cik=_int(_text(issuer, "issuerCik")),
        issuer_name=_text(issuer, "issuerName"),
        issuer_symbol=_text(issuer, "issuerTradingSymbol"),
        owners=owners,
        transactions=transactions,
    )
