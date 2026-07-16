"""All Postgres writes/reads for ingestion. Every filing write is an upsert on
accession_no, so re-running any step is idempotent (Day-2 requirement)."""
from __future__ import annotations

import datetime as dt
from typing import Iterable

from db.pool import connection

_FILING_COLS = (
    "accession_no", "cik", "company_name", "form_type", "filing_date",
    "primary_doc_url", "source_index_date", "is_amendment",
)


# --- dim_company -----------------------------------------------------------
def upsert_companies(rows: Iterable[tuple[int, str, str, str]]) -> int:
    """rows: (cik, ticker, title, source). Refreshes reference fields; leaves
    in_universe/module untouched."""
    rows = list(rows)
    if not rows:
        return 0
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO dim_company (cik, ticker, title, source) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (cik) DO UPDATE SET "
                "  ticker = EXCLUDED.ticker, title = EXCLUDED.title, "
                "  source = EXCLUDED.source, loaded_at = now()",
                rows,
            )
    return len(rows)


def mark_universe(cik: int, module: str, *, ticker: str | None = None,
                  title: str | None = None) -> None:
    """Flag a CIK as an in-scope universe member (insert owner CIKs if absent).
    Pins the canonical config ticker so share-class variants don't mislabel it."""
    with connection() as conn:
        conn.execute(
            "INSERT INTO dim_company (cik, ticker, title, source, in_universe, module) "
            "VALUES (%s, %s, %s, 'universe', TRUE, %s) "
            "ON CONFLICT (cik) DO UPDATE SET in_universe = TRUE, "
            "  module = EXCLUDED.module, "
            "  ticker = COALESCE(EXCLUDED.ticker, dim_company.ticker)",
            (cik, ticker, title, module),
        )


def universe_ciks() -> set[int]:
    with connection(readonly=True) as conn:
        return {r[0] for r in conn.execute(
            "SELECT cik FROM dim_company WHERE in_universe")}


def universe_members() -> list[tuple[int, str | None]]:
    with connection(readonly=True) as conn:
        return list(conn.execute(
            "SELECT cik, ticker FROM dim_company WHERE in_universe ORDER BY cik"))


# --- raw_filing ------------------------------------------------------------
def upsert_filings(rows: list[dict]) -> int:
    """Insert filing metadata; returns the number of NEW rows (skipped = dup)."""
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in _FILING_COLS) for r in rows]
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO raw_filing ({', '.join(_FILING_COLS)}) "
                f"VALUES ({', '.join(['%s'] * len(_FILING_COLS))}) "
                f"ON CONFLICT (accession_no) DO NOTHING",
                values,
            )
            return cur.rowcount


def filings_missing_docs(limit: int, form_prefix: str | None = None) -> list[tuple]:
    sql = ("SELECT accession_no, cik, form_type, filing_date, primary_doc_url "
           "FROM raw_filing WHERE NOT doc_stored ")
    params: list = []
    if form_prefix:
        sql += "AND form_type LIKE %s "
        params.append(form_prefix + "%")
    sql += "ORDER BY filing_date DESC NULLS LAST LIMIT %s"
    params.append(limit)
    with connection(readonly=True) as conn:
        return list(conn.execute(sql, params))


def mark_doc_stored(accession: str, path: str, sha256: str, nbytes: int) -> None:
    with connection() as conn:
        conn.execute(
            "UPDATE raw_filing SET doc_stored = TRUE, doc_path = %s, "
            "doc_sha256 = %s, doc_bytes = %s WHERE accession_no = %s",
            (path, sha256, nbytes, accession),
        )


# --- checkpoints -----------------------------------------------------------
def get_checkpoint(source: str) -> str | None:
    with connection(readonly=True) as conn:
        row = conn.execute(
            "SELECT cursor_value FROM ingest_checkpoint WHERE source = %s",
            (source,)).fetchone()
    return row[0] if row else None


def set_checkpoint(source: str, cursor_value: str, status: str, records: int) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO ingest_checkpoint (source, cursor_value, last_status, records) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (source) DO UPDATE SET cursor_value = EXCLUDED.cursor_value, "
            "  last_status = EXCLUDED.last_status, records = EXCLUDED.records, "
            "  updated_at = now()",
            (source, cursor_value, status, records),
        )


def count_filings() -> int:
    with connection(readonly=True) as conn:
        return conn.execute("SELECT count(*) FROM raw_filing").fetchone()[0]


# --- price_daily (market source) ---------------------------------------------
def max_price_date() -> dt.date | None:
    with connection(readonly=True) as conn:
        row = conn.execute("SELECT max(trade_date) FROM price_daily").fetchone()
    return row[0] if row else None


def upsert_prices(rows: list[tuple]) -> int:
    """rows: (ticker, trade_date, open, high, low, close, adj_close, volume).
    Upsert on (ticker, trade_date) — re-ingest refreshes late-corrected bars."""
    if not rows:
        return 0
    with connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO price_daily "
                "  (ticker, trade_date, open, high, low, close, adj_close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (ticker, trade_date) DO UPDATE SET "
                "  open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, "
                "  close = EXCLUDED.close, adj_close = EXCLUDED.adj_close, "
                "  volume = EXCLUDED.volume, loaded_at = now()",
                rows,
            )
    return len(rows)
