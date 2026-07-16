"""nlp/text.py: 10-K text extraction + customer-concentration passage retrieval."""
from __future__ import annotations

import gzip

from nlp import config
from nlp import text as nlp_text


def test_read_text_strips_markup(tmp_path):
    p = tmp_path / "10k.htm.gz"
    p.write_bytes(gzip.compress(
        b"<html><script>var x=1;</script><body><p>One  customer was\n 15% of"
        b" revenue.</p></body></html>"))
    out = nlp_text.read_text(str(p))
    assert out == "One customer was 15% of revenue."
    assert "var x" not in out


def _sentence(i: int) -> str:
    # The repeated unique marker fills the 240 chars *before* the % match, so
    # each window's dedup key (its first 60 chars) is distinct.
    return (f"Section {i:02d} marker. " * 14
            + f"One customer accounted for {10 + i}% of total revenue. ")


def test_concentration_passages_happy_path():
    text = "x" * 300 + "Our largest customer accounted for 25% of revenue." + "y" * 300
    passages = nlp_text.concentration_passages(text)
    assert len(passages) == 1
    assert "25%" in passages[0]
    # window is bounded by PASSAGE_WINDOW on each side of the % match
    assert len(passages[0]) <= 2 * config.PASSAGE_WINDOW + 10


def test_concentration_passages_requires_customer_and_revenue_words():
    # A percent with no customer mention nearby is not a passage.
    assert nlp_text.concentration_passages(
        "z" * 300 + "Gross margin improved to 45% of net sales." + "z" * 300) == []
    # A percent with a customer but no revenue word is not a passage either.
    assert nlp_text.concentration_passages(
        "z" * 300 + "One customer holds 45% of open orders." + "z" * 300) == []


def test_concentration_passages_dedups_overlapping_windows():
    # Two % hits in one short sentence produce identical window keys -> one passage.
    text = "Customer A was 15% and Customer B was 12% of revenue."
    assert len(nlp_text.concentration_passages(text)) == 1


def test_concentration_passages_caps_at_max():
    text = "".join(_sentence(i) for i in range(12))
    passages = nlp_text.concentration_passages(text)
    assert len(passages) == config.MAX_PASSAGES


def test_pct_regex_shape():
    assert config.PCT_RE.search("15 % of sales")
    assert config.PCT_RE.search("7.5% of sales")
    assert not config.PCT_RE.search("no percents here")
