"""Seed dim_company from company_tickers.json, then flag the curated universe.

Loading the full ~10k ticker->CIK map (cheap) means any CIK we later meet as a
counterparty can be named; the universe flag marks which ones we actively crawl.
"""
from __future__ import annotations

import logging

from ingestion import config, http_client, store

log = logging.getLogger(__name__)


def load_reference() -> int:
    """Upsert the whole company_tickers.json into dim_company (the reference)."""
    data = http_client.get_json(config.COMPANY_TICKERS_URL)
    rows = [(int(e["cik_str"]), e["ticker"], e["title"], "company_tickers")
            for e in data.values()]
    n = store.upsert_companies(rows)
    log.info("reference: upserted %d companies from company_tickers.json", n)
    return n


# v2: universe flagging moved to ingestion/universe.py (seed CSV ->
# universe_member table -> resolve_and_apply). reference.py now only loads the
# ~10k-company ticker->CIK map so any counterparty CIK can be named.
