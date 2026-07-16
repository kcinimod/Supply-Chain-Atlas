"""The company universe, as data.

v1 hardcoded the universe as a Python dict in config.py — changing scope meant
editing source. v2 stores it in `universe_member` (seeded from
db/seeds/universe.csv) and derives everything else:

    seed_from_csv()  csv -> universe_member (upsert; removed rows deactivate)
    resolve_and_apply()  resolve tickers -> CIK once, flag dim_company.in_universe

Downstream consumers use the accessors here instead of importing a dict:
    ticker_module_map(), supplier_tickers(), price_tickers(), members()
"""
from __future__ import annotations

import csv
import logging
import pathlib

from db.pool import connection
from ingestion import config, http_client, store

log = logging.getLogger(__name__)

SEED_CSV = pathlib.Path(__file__).resolve().parents[1] / "db" / "seeds" / "universe.csv"


# --- seeding -----------------------------------------------------------------
def seed_from_csv(path: pathlib.Path | None = None) -> int:
    """Upsert the seed CSV into universe_member. Members present in the table
    but absent from the CSV are deactivated (never deleted — history matters)."""
    path = path or SEED_CSV
    with path.open(newline="", encoding="utf-8") as f:
        rows = [
            {
                "ticker": (r["ticker"].strip().upper() or None),
                "cik": int(r["cik"]) if r["cik"].strip() else None,
                "title": r["title"].strip() or None,
                "module": r["module"].strip(),
                "side": r["side"].strip(),
            }
            for r in csv.DictReader(f)
        ]

    with connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                if r["ticker"] is not None:
                    cur.execute(
                        "INSERT INTO universe_member (ticker, cik, title, module, side, active) "
                        "VALUES (%(ticker)s, %(cik)s, %(title)s, %(module)s, %(side)s, TRUE) "
                        "ON CONFLICT (ticker) WHERE ticker IS NOT NULL DO UPDATE SET "
                        "  module = EXCLUDED.module, side = EXCLUDED.side, active = TRUE, "
                        "  title = COALESCE(EXCLUDED.title, universe_member.title), "
                        "  updated_at = now()",
                        r,
                    )
                else:
                    cur.execute(
                        "INSERT INTO universe_member (ticker, cik, title, module, side, active) "
                        "VALUES (%(ticker)s, %(cik)s, %(title)s, %(module)s, %(side)s, TRUE) "
                        "ON CONFLICT (cik) WHERE cik IS NOT NULL AND ticker IS NULL DO UPDATE SET "
                        "  module = EXCLUDED.module, side = EXCLUDED.side, active = TRUE, "
                        "  title = COALESCE(EXCLUDED.title, universe_member.title), "
                        "  updated_at = now()",
                        r,
                    )
            # Deactivate members dropped from the seed (ticker-keyed only; a
            # private owner is dropped by removing its CIK row from the CSV).
            seeded_tickers = [r["ticker"] for r in rows if r["ticker"]]
            seeded_ciks = [r["cik"] for r in rows if r["cik"] and not r["ticker"]]
            cur.execute(
                "UPDATE universe_member SET active = FALSE, updated_at = now() "
                "WHERE active AND ("
                "  (ticker IS NOT NULL AND NOT (ticker = ANY(%s)))"
                "  OR (ticker IS NULL AND NOT (cik = ANY(%s))))",
                (seeded_tickers, seeded_ciks),
            )
    log.info("universe: seeded %d members from %s", len(rows), path.name)
    return len(rows)


def resolve_and_apply() -> tuple[list[str], list[str]]:
    """Resolve every ticker-declared member to a CIK from company_tickers.json
    (one fetch), persist the CIK on universe_member, and flag
    dim_company.in_universe/module for the whole pipeline.

    Returns (resolved_tickers, unresolved_tickers)."""
    data = http_client.get_json(config.COMPANY_TICKERS_URL)
    ticker_to_cik = {e["ticker"].upper(): int(e["cik_str"]) for e in data.values()}

    resolved: list[str] = []
    unresolved: list[str] = []
    with connection() as conn:
        rows = list(conn.execute(
            "SELECT id, ticker, cik, title, module FROM universe_member WHERE active"))

    for member_id, ticker, cik, title, module in rows:
        if ticker is not None:
            cik = ticker_to_cik.get(ticker.upper())
            if cik is None:
                unresolved.append(ticker)
                continue
            with connection() as conn:
                conn.execute(
                    "UPDATE universe_member SET cik = %s, updated_at = now() WHERE id = %s",
                    (cik, member_id))
            store.mark_universe(cik, module, ticker=ticker)
            resolved.append(ticker)
        else:
            store.mark_universe(cik, module, title=title)

    log.info("universe: %d resolved, %d unresolved (%s)",
             len(resolved), len(unresolved), unresolved or "none")
    return resolved, unresolved


# --- accessors (replace the v1 config-dict readers) ---------------------------
def members(*, active_only: bool = True) -> list[dict]:
    sql = ("SELECT ticker, cik, title, module, side, active "
           "FROM universe_member")
    if active_only:
        sql += " WHERE active"
    sql += " ORDER BY module, side, ticker NULLS LAST"
    with connection(readonly=True) as conn:
        return [
            dict(zip(("ticker", "cik", "title", "module", "side", "active"), r))
            for r in conn.execute(sql)
        ]


def ticker_module_map() -> dict[str, str]:
    """ticker -> module for every active ticker-declared member."""
    with connection(readonly=True) as conn:
        return {t: m for t, m in conn.execute(
            "SELECT ticker, module FROM universe_member "
            "WHERE active AND ticker IS NOT NULL")}


def supplier_tickers() -> list[str]:
    """Tickers on the supplier side — whose 10-Ks carry the customer-
    concentration disclosures the NLP overlay reads."""
    with connection(readonly=True) as conn:
        return [r[0] for r in conn.execute(
            "SELECT ticker FROM universe_member "
            "WHERE active AND side = 'supplier' AND ticker IS NOT NULL "
            "ORDER BY ticker")]


def price_tickers() -> list[str]:
    """Every tradeable ticker to pull daily bars for, plus the benchmark."""
    with connection(readonly=True) as conn:
        tickers = [r[0] for r in conn.execute(
            "SELECT ticker FROM universe_member "
            "WHERE active AND ticker IS NOT NULL ORDER BY ticker")]
    if config.BENCHMARK_TICKER not in tickers:
        tickers.append(config.BENCHMARK_TICKER)
    return tickers
