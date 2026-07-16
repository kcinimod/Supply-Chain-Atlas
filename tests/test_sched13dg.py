"""transform/sched13dg.py: both eras -- structured XML and legacy text."""
from __future__ import annotations

import datetime as dt

from transform import sched13dg


# --- structured XML era (post-2024-12-18) ------------------------------------
def test_parse_xml_era_header(fixture_bytes):
    s = sched13dg.parse(fixture_bytes("sched13d_xml_era.txt"))

    assert s.source_format == "xml"
    assert s.submission_type == "SCHEDULE 13D"
    assert s.subject_cik == 1111111
    assert s.subject_name == "ATLAS SEMICONDUCTOR CORP"
    assert s.filer_cik == 2222222
    assert s.filer_name == "ORION ACTIVIST FUND LP"


def test_parse_xml_era_stakes(fixture_bytes):
    s = sched13dg.parse(fixture_bytes("sched13d_xml_era.txt"))

    assert s.cusip == "049164205"
    assert s.event_date == dt.date(2024, 12, 20)
    assert len(s.stakes) == 2

    fund = s.stakes[0]
    assert fund.reporting_person_name == "ORION ACTIVIST FUND LP"
    assert fund.class_percent == 6.2
    assert fund.aggregate_shares == 1500000.0
    assert fund.sole_voting == 1500000.0       # '1,500,000' comma tolerated
    assert fund.shared_voting == 0.0
    assert fund.sole_dispositive == 1500000.0
    assert fund.shared_dispositive == 0.0

    gp = s.stakes[1]                           # voting block absent -> None
    assert gp.reporting_person_name == "ORION GP HOLDINGS LLC"
    assert gp.sole_voting is None and gp.shared_voting is None


# --- legacy text era ----------------------------------------------------------
def test_parse_legacy_text_era(fixture_bytes):
    s = sched13dg.parse(fixture_bytes("sched13g_legacy.txt"))

    assert s.source_format == "text"
    assert s.submission_type == "SC 13G"
    assert s.subject_cik == 1111111
    assert s.filer_cik == 102909
    assert s.filer_name == "VANGUARD GROUP INC"
    assert s.cusip == "049164205"
    assert s.event_date is None                # legacy era has no event date

    assert len(s.stakes) == 1
    stake = s.stakes[0]
    assert stake.reporting_person_name == "VANGUARD GROUP INC"  # attributed to filer
    assert stake.class_percent == 7.5
    assert stake.aggregate_shares == 2345678.0


def test_legacy_percent_regex_crosses_row_numbers():
    """The common cover-page phrasing 'Percent of Class Represented by Amount
    in Row (11): 7.5%' must yield the percent — the skip window crosses the
    row number and captures the value adjacent to the '%'."""
    raw = (b"<SEC-HEADER>\nCONFORMED SUBMISSION TYPE:\tSC 13G\n"
           b"SUBJECT COMPANY:\n\tCENTRAL INDEX KEY:\t0000000001\n"
           b"FILED BY:\n\tCENTRAL INDEX KEY:\t0000000002\n</SEC-HEADER>\n"
           b"Percent of Class Represented by Amount in Row (11): 7.5%\n")
    s = sched13dg.parse(raw)
    assert s.source_format == "text"
    assert s.stakes[0].class_percent == 7.5


# --- parse_header edge cases ---------------------------------------------------
def test_parse_header_minimal():
    hdr = sched13dg.parse_header(
        "CONFORMED SUBMISSION TYPE:\tSCHEDULE 13G/A\n"
        "SUBJECT COMPANY:\n\tCOMPANY CONFORMED NAME: Target Co\n"
        "\tCENTRAL INDEX KEY: 0000000042\n"
        "FILED BY:\n\tCOMPANY CONFORMED NAME: Filer LLC\n"
        "\tCENTRAL INDEX KEY: 0000000007\n</SEC-HEADER>rest ignored")
    assert hdr["submission_type"] == "SCHEDULE 13G/A"
    assert hdr["subject_cik"] == 42 and hdr["subject_name"] == "Target Co"
    assert hdr["filer_cik"] == 7 and hdr["filer_name"] == "Filer LLC"


def test_parse_header_without_filed_by_keeps_subject():
    """A header missing the FILED BY marker must still yield the SUBJECT
    COMPANY fields (the subject block then runs to the end of the header)."""
    hdr = sched13dg.parse_header(
        "CONFORMED SUBMISSION TYPE:\tSC 13D\n"
        "SUBJECT COMPANY:\n\tCENTRAL INDEX KEY: 0000000042\n</SEC-HEADER>")
    assert hdr["subject_cik"] == 42
    assert hdr["filer_cik"] is None
    assert hdr["submission_type"] == "SC 13D"
