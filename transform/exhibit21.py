"""Locate and parse Exhibit 21 (subsidiaries of the registrant) from a 10-K.

Exhibit 21 is a separate document inside the 10-K submission with no consistent
filename or layout, so this module does two jobs:

* `find_exhibit_doc` picks the exhibit out of the submission's file list by name
  heuristic (`ex21` / `ex-21` / `subsidiar...`).
* `parse` reads the HTML table. The tricky part: many filers put the whole list
  in a *single* pair of cells (one line per subsidiary) rather than one row per
  subsidiary, so we split each cell on line breaks and zip the name column to the
  jurisdiction column. Falls back to one-name-per-line for non-table layouts.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

_EX21_NAME = re.compile(r"ex[-_]?21|subsidiar", re.IGNORECASE)

# Rows whose "name" is really a column header, not a subsidiary.
_HEADER_NAMES = {
    "name", "name of subsidiary", "subsidiary", "subsidiaries", "entity",
    "entity name", "company", "legal name", "name of entity",
}


def find_exhibit_doc(filenames: list[str]) -> str | None:
    """Choose the Exhibit 21 document from a submission's file list."""
    candidates = [
        f for f in filenames
        if _EX21_NAME.search(f) and f.lower().endswith((".htm", ".html", ".txt"))
    ]
    if not candidates:
        return None
    # Prefer an explicit "subsidiar..." filename, then the shortest name.
    candidates.sort(key=lambda f: (0 if "subsidiar" in f.lower() else 1, len(f)))
    return candidates[0]


def _clean(s: str) -> str:
    return s.replace("​", "").replace("\xa0", " ").strip()


def _cell_lines(cell) -> list[str]:
    return [c for c in (_clean(x) for x in cell.get_text("\n").split("\n")) if c]


def _is_header(name: str, jurisdiction: str | None) -> bool:
    n = name.lower()
    if n in _HEADER_NAMES or n.startswith(("subsidiaries of", "name of")):
        return True
    if jurisdiction and "jurisdiction" in n:
        return True
    return False


def parse(html_bytes: bytes) -> list[tuple[str, str | None]]:
    """Return [(subsidiary_name, jurisdiction_or_None), ...]. Passing raw bytes
    lets BeautifulSoup auto-detect the encoding (some filings are Latin-1)."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    pairs: list[tuple[str, str | None]] = []

    for tr in soup.find_all("tr"):
        cols = [_cell_lines(td) for td in tr.find_all(["td", "th"])]
        cols = [c for c in cols if c]          # drop empty spacer cells
        # Some filers prefix a row-number column (1, 2, 3, ...) -- drop it so the
        # name/jurisdiction columns aren't shifted.
        while len(cols) > 1 and all(re.fullmatch(r"\d+", x) for x in cols[0]):
            cols = cols[1:]
        if not cols:
            continue
        names = cols[0]
        juris = cols[1] if len(cols) >= 2 else []
        for i, name in enumerate(names):
            jurisdiction = juris[i] if i < len(juris) else None
            if not _is_header(name, jurisdiction):
                pairs.append((name, jurisdiction))

    # Non-table layout (a plain list): fall back to one name per text line, but
    # only lines that look like a legal entity, to avoid grabbing prose/titles.
    if not pairs:
        for line in (l for l in (_clean(x) for x in soup.get_text("\n").split("\n")) if l):
            if _looks_like_entity(line) and not _is_header(line, None):
                pairs.append((line, None))

    return pairs


_ENTITY_SUFFIX = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|gmbh|"
    r"s\.a|s\.r\.l|b\.v|pty|plc|lp|l\.p|holdings|sarl|kg|ag|nv|oy|ab|spa)\b\.?",
    re.IGNORECASE,
)


def _looks_like_entity(line: str) -> bool:
    return len(line) > 2 and bool(_ENTITY_SUFFIX.search(line))
