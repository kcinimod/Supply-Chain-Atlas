"""Read a stored 10-K and pull the customer-concentration passages (the only
part the LLM needs to see -- a 10-K is ~400k chars, far too much to send whole)."""
from __future__ import annotations

import gzip

from bs4 import BeautifulSoup

from ingestion import config as ing_config
from nlp import config


def read_text(doc_path: str) -> str:
    with gzip.open(ing_config.REPO_ROOT / doc_path.replace("\\", "/"), "rb") as fh:
        soup = BeautifulSoup(fh.read(), "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ").split())


def concentration_passages(text: str) -> list[str]:
    """Windows around a percent that also mention a customer and revenue."""
    out, seen = [], set()
    for m in config.PCT_RE.finditer(text):
        a = max(0, m.start() - config.PASSAGE_WINDOW)
        b = min(len(text), m.end() + config.PASSAGE_WINDOW)
        window = text[a:b]
        if config.CUSTOMER_RE.search(window) and config.REVENUE_RE.search(window):
            key = window[:60]
            if key not in seen:
                seen.add(key)
                out.append(window)
        if len(out) >= config.MAX_PASSAGES:
            break
    return out
