"""Settings + the target-form registry.

Design notes
------------
* The universe is scoped by COMPANY (a sector-coherent, mid-cap-tilted set),
  not by time window, so the supply-chain graph is dense and laptop-sized.
* Supply-chain edges (the ">=10% customer" disclosure) live in the SUPPLIER's
  10-K, so each module keeps suppliers AND their customers in-set.
* v2: the universe itself is DATA, not code — see db/seeds/universe.csv and
  ingestion/universe.py (universe_member table). Scope changes never touch
  source anymore.
"""
from __future__ import annotations

import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

# --- paths -----------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW_DIR = REPO_ROOT / "data" / "raw"
# Phase 2 keeps parsed-source XML in a separate bronze root so it never clobbers
# the Phase 1 rendered-document archive (whose sha256 is recorded in raw_filing).
RAW_XML_DIR = REPO_ROOT / "data" / "raw_xml"
# Columnar bronze for non-document sources (market bars land here as Parquet).
LAKE_DIR = REPO_ROOT / "data" / "lake"

# --- SEC access ------------------------------------------------------------
SEC_BASE = "https://www.sec.gov"
DATA_SEC_BASE = "https://data.sec.gov"
COMPANY_TICKERS_URL = f"{SEC_BASE}/files/company_tickers.json"

# SEC requires a descriptive User-Agent; defaults to the project owner so live
# calls work out of the box, overridable via .env.
USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Supply Chain Atlas research 2570455@sit.singaporetech.edu.sg",
)
RATE_LIMIT_RPS = float(os.environ.get("RATE_LIMIT_RPS", "8"))  # under SEC's 10/s
REQUEST_TIMEOUT = 30
DOC_FETCH_LIMIT = int(os.environ.get("DOC_FETCH_LIMIT", "200"))

# --- target forms ----------------------------------------------------------
# The structured relationship graph is built from these; 8-K is grabbed now for
# the later ML spine (8-K substance classification).
TARGET_FORMS = {
    "3", "4", "5", "3/A", "4/A", "5/A",            # insider -> company
    "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",    # beneficial owner -> holding (pre-2024-12-18)
    # SEC's structured-data mandate (effective 2024-12-18) renamed the Schedule
    # 13D/G form strings from "SC 13X" to "SCHEDULE 13X". Both spellings must be
    # accepted or every post-mandate ownership filing is silently dropped.
    "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
    "13F-HR", "13F-HR/A",                          # institution -> holdings
    "10-K", "10-K/A",                              # Exhibit 21 + customer concentration
    "8-K", "8-K/A",                                # material events (ML spine, later)
}

# --- market data -------------------------------------------------------------
# Benchmark for abnormal-return computation (reaction labels are measured
# relative to it, not raw).
BENCHMARK_TICKER = os.environ.get("BENCHMARK_TICKER", "SPY")
# Earliest daily bar to backfill; a decade of history is plenty for training
# reaction models over historical filing events.
PRICE_HISTORY_START = os.environ.get("PRICE_HISTORY_START", "2015-01-01")

# NOTE (v2): the universe dict + ticker_to_module()/universe_tickers() moved to
# the universe_member table — see ingestion/universe.py for the accessors.
