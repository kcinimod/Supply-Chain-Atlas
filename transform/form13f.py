"""Parse a 13F-HR submission into the manager's holdings.

A 13F submission bundles two XML documents inside the full-submission .txt: a
cover page (`edgarSubmission` -> period of report, filing manager) and the
`informationTable` (one `infoTable` per holding). We slice each element out of
the SGML wrapper and parse it, so we need only one fetch per filing. Namespaces
are stripped because the info table's schema is namespaced in some vintages and
bare in others.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

# SEC switched 13F `value` from thousands-of-dollars to whole dollars for filings
# made on/after this date (the 2022-Q4 report).
_WHOLE_DOLLAR_CUTOVER = dt.date(2023, 1, 3)


@dataclass
class Holding:
    name_of_issuer: str | None
    title_of_class: str | None
    cusip: str | None
    value_reported: float | None
    shares: float | None
    shares_type: str | None
    investment_discretion: str | None
    voting_sole: float | None
    voting_shared: float | None
    voting_none: float | None


@dataclass
class Filing13F:
    period_of_report: dt.date | None
    manager_name: str | None
    holdings: list[Holding] = field(default_factory=list)


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.rsplit("}", 1)[1]
    return root


def _slice(raw: bytes, tag: str) -> bytes | None:
    """Cut the <tag>..</tag> XML island out of the SGML envelope. Tolerates a
    namespace *prefix* on the root element (<ns1:informationTable>), which some
    filers emit — a plain byte-find on "<informationTable" would miss those."""
    pat = re.escape(tag.encode())
    open_m = re.search(rb"<(?:[A-Za-z0-9_.-]+:)?" + pat + rb"[\s>]", raw)
    close_iter = list(re.finditer(rb"</(?:[A-Za-z0-9_.-]+:)?" + pat + rb"\s*>", raw))
    if open_m is None or not close_iter:
        return None
    return raw[open_m.start():close_iter[-1].end()]


def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.findtext(path)
    return found.strip() or None if found is not None else None


def _float(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _period_date(s: str | None) -> dt.date | None:
    """13F periodOfReport is MM-DD-YYYY; be tolerant of ISO too."""
    if not s:
        return None
    for fmt in ("%m-%d-%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


def _holding(it: ET.Element) -> Holding:
    return Holding(
        name_of_issuer=_text(it, "nameOfIssuer"),
        title_of_class=_text(it, "titleOfClass"),
        cusip=_text(it, "cusip"),
        value_reported=_float(_text(it, "value")),
        shares=_float(_text(it, "shrsOrPrnAmt/sshPrnamt")),
        shares_type=_text(it, "shrsOrPrnAmt/sshPrnamtType"),
        investment_discretion=_text(it, "investmentDiscretion"),
        voting_sole=_float(_text(it, "votingAuthority/Sole")),
        voting_shared=_float(_text(it, "votingAuthority/Shared")),
        voting_none=_float(_text(it, "votingAuthority/None")),
    )


def value_to_usd(value_reported: float | None, filing_date: dt.date | None) -> float | None:
    """Normalize the reported value to whole dollars using the filing date."""
    if value_reported is None:
        return None
    if filing_date is not None and filing_date < _WHOLE_DOLLAR_CUTOVER:
        return value_reported * 1000.0
    return value_reported


def parse(submission_bytes: bytes) -> Filing13F:
    """Parse a full 13F submission .txt (or the two XML docs concatenated)."""
    cover_xml = _slice(submission_bytes, "edgarSubmission")
    period = manager = None
    if cover_xml is not None:
        cover = _strip_ns(ET.fromstring(cover_xml))
        period = _period_date(_text(cover, ".//periodOfReport"))
        manager = _text(cover, ".//filingManager/name")

    table_xml = _slice(submission_bytes, "informationTable")
    holdings: list[Holding] = []
    if table_xml is not None:
        table = _strip_ns(ET.fromstring(table_xml))
        holdings = [_holding(it) for it in table.findall(".//infoTable")]

    return Filing13F(period_of_report=period, manager_name=manager, holdings=holdings)
