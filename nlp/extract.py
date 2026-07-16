"""LLM extraction of customer-concentration facts from the retrieved passages,
with two post-filters:
  * placeholder demotion — 'Customer A', 'one customer' etc. are not names;
  * pct-in-quote verification — a percentage is kept only if its number
    literally appears in the model's own source quote. The A/B study showed
    every model variant FABRICATES a number when the filing says only
    'more than 10%'; grounding the pct in the quote kills that failure mode
    (the edge survives, the invented number does not)."""
from __future__ import annotations

import logging
import re

from nlp import config, ollama_client

log = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _is_real_name(name: str, named_flag: bool) -> bool:
    """The model's own `named` flag, tightened: 'Customer A', 'one customer',
    'our largest customer' etc. are placeholders, not resolvable companies."""
    if not named_flag or not name:
        return False
    return not config.ANON_RE.match(name.strip())


def _grounded_pct(pct, quote: str):
    """Return pct only if its numeric value appears verbatim in the quote
    (10 matches '10', '10%', '10.0'); otherwise None (fabrication guard)."""
    if pct is None or not quote:
        return None
    try:
        val = float(pct)
    except (TypeError, ValueError):
        return None
    for m in _NUM_RE.finditer(quote):
        try:
            if abs(float(m.group(0)) - val) < 1e-9:
                return pct
        except ValueError:
            continue
    return None


def extract_facts(passages: list[str]) -> list[dict]:
    """Return [{name, pct_of_revenue, is_named, quote}] for one filing."""
    if not passages:
        return []
    body = "\n---\n".join(passages)[:config.MAX_PROMPT_CHARS]
    result = ollama_client.chat_json(config.EXTRACT_PROMPT + body, config.EXTRACT_SCHEMA)

    facts, seen = [], set()
    for c in result.get("customers", []):
        name = (c.get("name") or "").strip()
        if not name:
            continue
        is_named = _is_real_name(name, bool(c.get("named")))
        quote = (c.get("quote") or "")[:600]
        pct = _grounded_pct(c.get("pct_of_revenue"), quote)
        # dedup by customer name: repeated mentions of the same counterparty in
        # one filing collapse to the first fact (grounding can null a repeat's
        # pct, so a value-sensitive key would leak duplicates back in)
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        facts.append({
            "name": name if is_named else "unnamed",
            "pct_of_revenue": pct,
            "is_named": is_named,
            "quote": quote,
        })
    return facts
