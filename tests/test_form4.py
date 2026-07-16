"""transform/form4.py: ownership-document XML -> Form4 dataclass."""
from __future__ import annotations

import datetime as dt

import pytest

from transform import form4


def test_parse_full_submission_sgml_envelope(fixture_bytes):
    """The SGML full-submission .txt wraps the XML; the parser slices it out."""
    f = form4.parse(fixture_bytes("form4_full_submission.txt"))

    assert f.document_type == "4"
    assert f.period_of_report == dt.date(2024, 5, 1)
    assert f.issuer_cik == 1045810
    assert f.issuer_name == "NVIDIA CORP"
    assert f.issuer_symbol == "NVDA"


def test_parse_owners(fixture_bytes):
    f = form4.parse(fixture_bytes("form4_full_submission.txt"))

    assert len(f.owners) == 2
    primary = f.owners[0]
    assert primary.cik == 1234567
    assert primary.name == "DOE JANE"
    assert primary.is_director is False
    assert primary.is_officer is True
    assert primary.is_ten_pct_owner is False   # 'false' spelling handled
    assert primary.officer_title == "Chief Financial Officer"

    # Second owner has no relationship block at all -> all flags default False.
    trust = f.owners[1]
    assert trust.cik is None
    assert trust.name == "DOE FAMILY TRUST"
    assert trust.is_director is False and trust.is_officer is False
    assert trust.officer_title is None


def test_parse_transactions(fixture_bytes):
    f = form4.parse(fixture_bytes("form4_full_submission.txt"))

    assert len(f.transactions) == 2
    sale, option = f.transactions

    assert sale.is_derivative is False
    assert sale.security_title == "Common Stock"
    assert sale.transaction_date == dt.date(2024, 5, 1)
    assert sale.transaction_code == "S"
    assert sale.shares == 1000.0
    assert sale.price_per_share == 892.50
    assert sale.acquired_disposed == "D"
    assert sale.shares_owned_following == 54321.0
    assert sale.direct_indirect == "D"

    assert option.is_derivative is True
    assert option.transaction_code == "M"
    assert option.price_per_share is None      # missing optional field -> None
    assert option.acquired_disposed == "A"
    assert option.direct_indirect == "I"


def test_parse_clean_doc_xml_with_declaration(fixture_bytes):
    """A bare doc.xml (leading <?xml?> declaration, no SGML) parses identically."""
    f = form4.parse(fixture_bytes("form4_doc.xml"))

    assert f.document_type == "4"
    # periodOfReport is '05/01/2024' -- not ISO, tolerated as None, not an error.
    assert f.period_of_report is None
    assert f.issuer_cik == 883241
    assert f.issuer_symbol is None             # optional field absent
    assert f.owners[0].is_director is True     # 'true' spelling handled
    assert f.transactions == []                # no transaction tables


def test_parse_rejects_non_ownership_document():
    with pytest.raises(ValueError, match="ownershipDocument"):
        form4.parse(b"<html><body>not a form 4</body></html>")


def test_helpers_tolerate_garbage():
    assert form4._int("notanumber") is None
    assert form4._float("1,000") == 1000.0     # comma-grouped counts parse
    assert form4._date("2024-13-99") is None
    assert form4._bool(None) is False
    assert form4._bool(" TRUE ") is True
