"""ml/text.py: gzipped HTML -> clean plain text with 8-K item codes scrubbed.

extract_text joins its argument onto REPO_ROOT with pathlib, so an *absolute*
tmp path passes through unchanged -- no repo files are touched.
"""
from __future__ import annotations

import gzip

from ml import text as ml_text


def _gz(tmp_path, html: str) -> str:
    p = tmp_path / "doc.htm.gz"
    p.write_bytes(gzip.compress(html.encode("utf-8")))
    return str(p)


def test_extract_text_strips_markup_scripts_and_whitespace(tmp_path):
    html = ("<html><head><style>p {color: red}</style>"
            "<script>alert('leak')</script></head>"
            "<body><p>Revenue   grew\n\n substantially</p></body></html>")
    out = ml_text.extract_text(_gz(tmp_path, html))
    assert out == "Revenue grew substantially"
    assert "alert" not in out and "color" not in out


def test_extract_text_scrubs_item_codes(tmp_path):
    html = ("<body><p>Item 2.02 Results of Operations. "
            "See Item&nbsp;9.01 below. Also 7.01 was referenced.</p></body>")
    out = ml_text.extract_text(_gz(tmp_path, html))
    # Both 'Item N.NN' and bare 'N.NN' codes are removed.
    assert "2.02" not in out
    assert "9.01" not in out
    assert "7.01" not in out
    assert "Item" not in out           # the literal word before a code goes too
    assert "Results of Operations" in out


def test_extract_text_keeps_non_code_numbers(tmp_path):
    # One-decimal numbers are not item codes and must survive.
    out = ml_text.extract_text(_gz(tmp_path, "<body>margin was 12.5 percent</body>"))
    assert "12.5" in out


def test_extract_text_max_chars(tmp_path):
    out = ml_text.extract_text(_gz(tmp_path, "<body>" + "word " * 100 + "</body>"),
                               max_chars=11)
    assert out == "word word w"
