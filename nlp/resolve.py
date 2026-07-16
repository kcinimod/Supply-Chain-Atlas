"""Entity resolution: an extracted customer name -> an in-universe CIK.

The universe was built so a supplier's disclosed customer is usually in-set, so
this is mostly a normalise-and-match problem, helped by an alias map for the
household names that appear in prose ('NVIDIA' -> NVDA). Anything that doesn't
match stays a name-only node -- honest partial coverage, like Exhibit 21 subs.
"""
from __future__ import annotations

import re

_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "llc", "lp", "holdings", "holding", "technologies", "technology",
    "systems", "system", "group", "international", "industries", "sa", "ag", "nv",
    "se", "the", "and", "grp", "communications", "semiconductor", "semiconductors",
}

# Prose names -> canonical ticker (short-circuits normalise-matching for the
# household customers that show up by their common name, not their legal name).
ALIAS = {
    "nvidia": "NVDA", "advanced micro devices": "AMD", "amd": "AMD",
    "applied materials": "AMAT", "lam research": "LRCX", "marvell": "MRVL",
    "equinix": "EQIX", "digital realty": "DLR", "tesla": "TSLA",
    "general motors": "GM", "ford": "F", "ford motor": "F", "rivian": "RIVN",
    "lockheed martin": "LMT", "lockheed": "LMT", "raytheon": "RTX", "rtx": "RTX",
    "northrop grumman": "NOC", "northrop": "NOC", "general dynamics": "GD",
    "boeing": "BA", "eli lilly": "LLY", "lilly": "LLY", "pfizer": "PFE",
    "merck": "MRK", "amgen": "AMGN",
}


def normalize(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    toks = [t for t in name.split() if t and t not in _SUFFIXES]
    return " ".join(toks)


class Resolver:
    """Build once from dim_company; resolve many names."""

    def __init__(self, companies: list[tuple]):
        # companies: (cik, ticker, company_name, in_universe)
        self.by_ticker, self.by_norm = {}, {}
        for cik, ticker, company_name, in_universe in companies:
            if not in_universe:
                continue
            if ticker:
                self.by_ticker[ticker.upper()] = cik
            if company_name:
                self.by_norm.setdefault(normalize(company_name), cik)

    def resolve(self, name: str) -> int | None:
        norm = normalize(name)
        if not norm:
            return None
        if norm in ALIAS and ALIAS[norm] in self.by_ticker:
            return self.by_ticker[ALIAS[norm]]
        if norm in self.by_norm:
            return self.by_norm[norm]
        # leading-token match: extracted "nvidia" vs company "nvidia" first token
        head = norm.split()[0]
        for cnorm, cik in self.by_norm.items():
            if cnorm.split() and cnorm.split()[0] == head and len(head) > 2:
                return cik
        return None
