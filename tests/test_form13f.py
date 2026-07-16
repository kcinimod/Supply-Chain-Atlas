"""transform/form13f.py: 13F-HR full submission -> Filing13F + value normalization."""
from __future__ import annotations

import datetime as dt

from transform import form13f


def test_parse_cover_page(fixture_bytes):
    f = form13f.parse(fixture_bytes("form13f_full_submission.txt"))

    assert f.period_of_report == dt.date(2022, 12, 31)   # MM-DD-YYYY handled
    assert f.manager_name == "EXAMPLE CAPITAL MANAGEMENT LLC"


def test_parse_holdings(fixture_bytes):
    f = form13f.parse(fixture_bytes("form13f_full_submission.txt"))

    assert len(f.holdings) == 3
    nvda = f.holdings[0]
    assert nvda.name_of_issuer == "NVIDIA CORP"
    assert nvda.title_of_class == "COM"
    assert nvda.cusip == "67066G104"
    assert nvda.value_reported == 146250.0
    assert nvda.shares == 1000000.0
    assert nvda.shares_type == "SH"
    assert nvda.investment_discretion == "SOLE"
    assert nvda.voting_sole == 1000000.0
    assert nvda.voting_shared == 0.0
    assert nvda.voting_none == 0.0

    # Comma-formatted value is tolerated.
    assert f.holdings[1].value_reported == 9876.0

    # Sparse infoTable: missing optional fields all come back None.
    sparse = f.holdings[2]
    assert sparse.name_of_issuer == "MYSTERY HOLDCO"
    assert sparse.value_reported is None
    assert sparse.shares is None
    assert sparse.voting_sole is None


def test_parse_bare_unnamespaced_table():
    """Some vintages ship the info table without any xmlns; both must parse."""
    raw = (b"<informationTable><infoTable>"
           b"<nameOfIssuer>ACME CORP</nameOfIssuer><value>42</value>"
           b"</infoTable></informationTable>")
    f = form13f.parse(raw)
    assert f.period_of_report is None          # no cover page present
    assert len(f.holdings) == 1
    assert f.holdings[0].name_of_issuer == "ACME CORP"
    assert f.holdings[0].value_reported == 42.0


def test_parse_iso_period_tolerated():
    raw = (b"<edgarSubmission><headerData><filerInfo>"
           b"<periodOfReport>2022-12-31</periodOfReport>"
           b"</filerInfo></headerData></edgarSubmission>")
    assert form13f.parse(raw).period_of_report == dt.date(2022, 12, 31)


def test_parse_no_xml_returns_empty():
    f = form13f.parse(b"just some text, no xml documents at all")
    assert f.period_of_report is None
    assert f.manager_name is None
    assert f.holdings == []


def test_prefixed_information_table_is_sliced_and_parsed():
    """A namespace-PREFIXED root tag (<ns1:informationTable>) must be sliced
    out of the envelope just like the bare spelling — some filers emit it."""
    raw = (b'<ns1:informationTable xmlns:ns1="http://www.sec.gov/edgar/document/'
           b'thirteenf/informationtable"><ns1:infoTable>'
           b"<ns1:nameOfIssuer>ACME</ns1:nameOfIssuer>"
           b"</ns1:infoTable></ns1:informationTable>")
    holdings = form13f.parse(raw).holdings
    assert len(holdings) == 1
    assert holdings[0].name_of_issuer == "ACME"


# --- thousands -> whole-dollar cutover --------------------------------------
def test_value_to_usd_before_cutover_multiplies_by_1000():
    assert form13f.value_to_usd(146250.0, dt.date(2022, 12, 31)) == 146250000.0
    assert form13f.value_to_usd(146250.0, dt.date(2023, 1, 2)) == 146250000.0


def test_value_to_usd_on_and_after_cutover_is_whole_dollars():
    assert form13f.value_to_usd(146250.0, dt.date(2023, 1, 3)) == 146250.0
    assert form13f.value_to_usd(146250.0, dt.date(2024, 6, 1)) == 146250.0


def test_value_to_usd_edge_inputs():
    assert form13f.value_to_usd(None, dt.date(2022, 1, 1)) is None
    # Unknown filing date -> assume whole dollars (no multiplication).
    assert form13f.value_to_usd(500.0, None) == 500.0
