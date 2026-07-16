"""Turn a stored bronze 8-K document into clean plain text (the model features).

Bronze docs are gzipped HTML (primary doc) or SGML (.txt full submission). We
strip markup and collapse whitespace -- good enough for a TF-IDF baseline; the
documented upgrade is sentence-embeddings over the same text (pgvector + GPU).
"""
from __future__ import annotations

import gzip
import re

from bs4 import BeautifulSoup

from ingestion import config

# An 8-K body reprints its own Item headers ("Item 2.02 Results of Operations").
# The Item NUMBER is exactly what the label is derived from, so leaving it in the
# features would let the model read the label back -- leakage. Strip the explicit
# codes (and the literal word "item" before them) so the classifier must learn
# materiality from the narrative language, not the printed code.
_ITEM_CODE = re.compile(r"(?i)\bitem\s*\d{1,2}\.\d{2}\b|\b\d{1,2}\.\d{2}\b")


def extract_text(doc_path: str, *, max_chars: int = 40_000) -> str:
    """Read REPO_ROOT/doc_path (a gzipped doc) and return collapsed plain text,
    with explicit 8-K Item codes scrubbed to prevent label leakage.

    doc_path may carry Windows separators (ingested on the host); normalize so
    the same row resolves inside the Linux Airflow containers too."""
    path = config.REPO_ROOT / doc_path.replace("\\", "/")
    with gzip.open(path, "rb") as fh:
        raw = fh.read()
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ")
    text = _ITEM_CODE.sub(" ", text)
    text = " ".join(text.split())
    return text[:max_chars]
