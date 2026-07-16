"""transform/exhibit21.py: exhibit selection + subsidiary-table parsing."""
from __future__ import annotations

from transform import exhibit21


# --- find_exhibit_doc --------------------------------------------------------
def test_find_exhibit_doc_prefers_subsidiaries_name():
    files = ["aapl-20240928.htm", "ex21.htm", "subsidiaries.htm", "ex-21_1.htm"]
    assert exhibit21.find_exhibit_doc(files) == "subsidiaries.htm"


def test_find_exhibit_doc_shortest_name_tiebreak():
    files = ["abcd-ex21_1.htm", "ex21.htm"]
    assert exhibit21.find_exhibit_doc(files) == "ex21.htm"


def test_find_exhibit_doc_matches_variants_and_extensions():
    assert exhibit21.find_exhibit_doc(["ex_21.txt"]) == "ex_21.txt"
    assert exhibit21.find_exhibit_doc(["EX-21.HTM"]) == "EX-21.HTM"
    # Wrong extension (graphics, XBRL) never selected.
    assert exhibit21.find_exhibit_doc(["ex21.jpg", "ex21.xml"]) is None
    assert exhibit21.find_exhibit_doc([]) is None
    assert exhibit21.find_exhibit_doc(["10k.htm", "ex99_1.htm"]) is None


# --- parse: table layouts ----------------------------------------------------
def test_parse_table(fixture_bytes):
    pairs = exhibit21.parse(fixture_bytes("exhibit21_table.htm"))

    assert pairs == [
        ("Atlas Semiconductor GmbH", "Germany"),          # row-number col dropped
        ("Atlas Components (Shanghai) Co., Ltd.", "China"),
        ("Atlas Holdings LLC", None),                     # zwsp/&nbsp; cleaned
        ("Zeta Manufacturing, Inc.", "Delaware"),         # single-cell multi-line
        ("Zeta International B.V.", "Netherlands"),
    ]


def test_parse_table_excludes_header_rows(fixture_bytes):
    names = [n for n, _ in exhibit21.parse(fixture_bytes("exhibit21_table.htm"))]
    assert "Name of Subsidiary" not in names
    assert "Jurisdiction of Incorporation" not in names
    assert "1" not in names and "2" not in names


# --- parse: non-table fallback -----------------------------------------------
def test_parse_plain_list_fallback(fixture_bytes):
    pairs = exhibit21.parse(fixture_bytes("exhibit21_plain.htm"))

    assert pairs == [
        ("Orion Photonics, Inc.", None),
        ("Orion Photonics GmbH", None),
        ("Orion Photonics (UK) Limited", None),
    ]


def test_parse_nothing_useful_returns_empty():
    assert exhibit21.parse(b"<html><body><p>No entities here.</p></body></html>") == []


def test_looks_like_entity():
    assert exhibit21._looks_like_entity("Acme Widgets, Inc.")
    assert exhibit21._looks_like_entity("Acme GmbH")
    assert not exhibit21._looks_like_entity("Delaware")
    assert not exhibit21._looks_like_entity("Co")   # too short on its own
