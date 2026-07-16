"""Parse a Schedule 13D/13G submission into beneficial-ownership stakes.

Two eras, one interface:
* The **SGML SEC-HEADER** (present in every full-submission .txt) yields the
  subject-company CIK and the filer CIK -- the graph edge -- in both eras.
* Post-2024-12-18 filings embed a structured ``<edgarSubmission>`` XML with the
  CUSIP, event date, and one detail block per reporting person (name, % of class,
  shares, voting/dispositive power). We slice and parse it.
* Legacy filings have only a text cover page; we regex the headline % of class and
  aggregate shares (best-effort, single stake attributed to the filer).
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class Stake:
    reporting_person_name: str | None
    class_percent: float | None
    aggregate_shares: float | None
    sole_voting: float | None = None
    shared_voting: float | None = None
    sole_dispositive: float | None = None
    shared_dispositive: float | None = None


@dataclass
class Schedule13:
    submission_type: str | None
    source_format: str
    filer_cik: int | None
    filer_name: str | None
    subject_cik: int | None
    subject_name: str | None
    cusip: str | None
    event_date: dt.date | None
    stakes: list[Stake] = field(default_factory=list)


# --- shared helpers --------------------------------------------------------
def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.rsplit("}", 1)[1]
    return root


def _slice(raw: bytes, tag: str) -> bytes | None:
    """Cut the <tag>..</tag> XML island out of the SGML envelope, tolerating a
    namespace prefix on the root element (same fix as form13f._slice)."""
    pat = re.escape(tag.encode())
    open_m = re.search(rb"<(?:[A-Za-z0-9_.-]+:)?" + pat + rb"[\s>]", raw)
    close_iter = list(re.finditer(rb"</(?:[A-Za-z0-9_.-]+:)?" + pat + rb"\s*>", raw))
    if open_m is None or not close_iter:
        return None
    return raw[open_m.start():close_iter[-1].end()]


def _f(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return None


def _mdy(s: str | None) -> dt.date | None:
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s.strip()[:10], fmt).date()
        except ValueError:
            continue
    return None


# --- SGML header (both eras) ----------------------------------------------
def parse_header(text: str) -> dict:
    hdr = text[: text.find("</SEC-HEADER>")] if "</SEC-HEADER>" in text else text[:6000]
    subj_start = hdr.find("SUBJECT COMPANY")
    filed_start = hdr.find("FILED BY")
    # Subject block ends at FILED BY when present; some headers omit FILED BY
    # entirely and the subject fields must still parse.
    subj_end = filed_start if filed_start >= 0 else len(hdr)
    subj = hdr[subj_start:subj_end] if subj_start >= 0 else ""
    filed = hdr[filed_start:] if filed_start >= 0 else ""

    def cik(block: str) -> int | None:
        m = re.search(r"CENTRAL INDEX KEY:\s*(\d+)", block)
        return int(m.group(1)) if m else None

    def name(block: str) -> str | None:
        m = re.search(r"COMPANY CONFORMED NAME:\s*(.+)", block)
        return m.group(1).strip() if m else None

    subtype = re.search(r"CONFORMED SUBMISSION TYPE:\s*(.+)", hdr)
    return {
        "submission_type": subtype.group(1).strip() if subtype else None,
        "subject_cik": cik(subj), "subject_name": name(subj),
        "filer_cik": cik(filed), "filer_name": name(filed),
    }


# --- structured XML era ----------------------------------------------------
def _text(el: ET.Element | None, path: str) -> str | None:
    if el is None:
        return None
    found = el.findtext(path)
    return found.strip() or None if found is not None else None


def _parse_xml(xml_bytes: bytes) -> tuple[str | None, dt.date | None, list[Stake]]:
    root = _strip_ns(ET.fromstring(xml_bytes))
    cusip = _text(root, ".//issuerCusipNumber")
    event = _mdy(_text(root, ".//eventDateRequiresFilingThisStatement"))
    stakes: list[Stake] = []
    for d in root.findall(".//coverPageHeaderReportingPersonDetails"):
        stakes.append(Stake(
            reporting_person_name=_text(d, "reportingPersonName"),
            class_percent=_f(_text(d, "classPercent")),
            aggregate_shares=_f(_text(
                d, "reportingPersonBeneficiallyOwnedAggregateNumberOfShares")),
            sole_voting=_f(_text(d, "reportingPersonBeneficiallyOwnedNumberOfShares/soleVotingPower")),
            shared_voting=_f(_text(d, "reportingPersonBeneficiallyOwnedNumberOfShares/sharedVotingPower")),
            sole_dispositive=_f(_text(d, "reportingPersonBeneficiallyOwnedNumberOfShares/soleDispositivePower")),
            shared_dispositive=_f(_text(d, "reportingPersonBeneficiallyOwnedNumberOfShares/sharedDispositivePower")),
        ))
    return cusip, event, stakes


# --- legacy text era -------------------------------------------------------
# The cover-page phrasing is often "Percent of Class Represented by Amount in
# Row (11): 7.5%" — the skip window must be able to cross the row NUMBER, so it
# excludes only '%' (not digits) and is length-bounded to stay on the item.
_PCT = re.compile(r"percent\s+of\s+class[^%]{0,160}?([\d]+(?:\.\d+)?)\s*%", re.IGNORECASE)
_AGG = re.compile(r"aggregate\s+amount\s+beneficially\s+owned[^\d]*?([\d,]+)", re.IGNORECASE)
_CUSIP = re.compile(r"\b([0-9A-Z]{6}[0-9A-Z]{2}[0-9])\b\s*\(?\s*cusip", re.IGNORECASE)


def _parse_text(text: str, filer_name: str | None) -> tuple[str | None, list[Stake]]:
    # max(idx, 0): a document with no </SEC-HEADER> should scan the whole text,
    # not text[-1:] (find() returning -1 would silently slice the last char).
    plain = re.sub(r"<[^>]+>", " ", text[max(text.find("</SEC-HEADER>"), 0):])
    plain = re.sub(r"&#?\w+;", " ", plain)
    plain = re.sub(r"\s+", " ", plain)
    pct = _PCT.search(plain)
    agg = _AGG.search(plain)
    cusip = _CUSIP.search(plain)
    stake = Stake(
        reporting_person_name=filer_name,
        class_percent=_f(pct.group(1)) if pct else None,
        aggregate_shares=_f(agg.group(1)) if agg else None,
    )
    return (cusip.group(1) if cusip else None), [stake]


# --- entry point -----------------------------------------------------------
def parse(submission_bytes: bytes) -> Schedule13:
    text = submission_bytes.decode("utf-8", "replace")
    hdr = parse_header(text)

    xml = _slice(submission_bytes, "edgarSubmission")
    if xml is not None:
        cusip, event, stakes = _parse_xml(xml)
        source = "xml"
    else:
        cusip, stakes = _parse_text(text, hdr["filer_name"])
        event, source = None, "text"

    return Schedule13(
        submission_type=hdr["submission_type"], source_format=source,
        filer_cik=hdr["filer_cik"], filer_name=hdr["filer_name"],
        subject_cik=hdr["subject_cik"], subject_name=hdr["subject_name"],
        cusip=cusip, event_date=event, stakes=stakes,
    )
