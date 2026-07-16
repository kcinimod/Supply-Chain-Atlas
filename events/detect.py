"""Change detectors: turn the gold layer's *state* into discrete EVENTS.

Each detector scans a trailing window (default 30 days — comfortably wider
than any laptop-off gap) and upserts into filing_event; the natural-key
uniqueness makes overlapping scans no-ops, so the window can be generous.

Detectors:
    material_8k         a material 8-K landed (label from Item codes; the ML
                        classifier's score is attached when available)
    supply_edge         a named supply edge appeared for the first time, or its
                        revenue-concentration pct moved >= 2pp vs the prior 10-K
    stake_change        a 13D/G stake version crossed 5%/10% or moved >= 2pp
    insider_cluster     >= 3 distinct insiders open-market-bought one company
                        inside the scan window
"""
from __future__ import annotations

import datetime as dt
import logging

from db.pool import connection
from events import store

log = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30
PCT_MOVE_THRESHOLD = 2.0        # pp move in supply concentration / stake
STAKE_LEVELS = (5.0, 10.0)      # regulatory-meaningful ownership thresholds
CLUSTER_MIN_INSIDERS = 3


def _since(window_days: int | None) -> dt.date:
    return dt.date.today() - dt.timedelta(days=window_days or DEFAULT_WINDOW_DAYS)


def detect_material_8k(since: dt.date) -> list[dict]:
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT i.accession_no, i.cik, i.filing_date, i.item_codes,
                   p.proba, p.model_version
            FROM eightk_item i
            LEFT JOIN LATERAL (
                SELECT proba, model_version FROM eightk_prediction p
                WHERE p.accession_no = i.accession_no
                ORDER BY p.scored_at DESC LIMIT 1
            ) p ON TRUE
            WHERE i.is_material AND i.filing_date >= %s
            """, (since,)).fetchall()
    tickers = store.ticker_by_cik()
    return [{
        "event_type": "material_8k",
        "cik": cik,
        "ticker": tickers.get(cik),
        "event_date": filing_date,
        "accession_no": acc,
        "details": {"item_codes": item_codes, "clf_proba": float(proba) if proba is not None else None,
                    "clf_model_version": model_version},
    } for acc, cik, filing_date, item_codes, proba, model_version in rows]


def detect_supply_edges(since: dt.date) -> list[dict]:
    with connection(readonly=True) as conn:
        current = conn.execute(
            """
            SELECT r.supplier_cik, r.customer_name_raw, r.customer_cik,
                   r.pct_of_revenue, r.source_accession, rf.filing_date
            FROM supply_relationship r
            JOIN raw_filing rf ON rf.accession_no = r.source_accession
            WHERE r.is_named AND rf.filing_date >= %s
            """, (since,)).fetchall()

        events: list[dict] = []
        tickers = store.ticker_by_cik()
        for supplier, cust_name, cust_cik, pct, acc, fdate in current:
            prev = conn.execute(
                """
                SELECT r2.pct_of_revenue
                FROM supply_relationship r2
                JOIN raw_filing rf2 ON rf2.accession_no = r2.source_accession
                WHERE r2.supplier_cik = %s
                  AND lower(r2.customer_name_raw) = lower(%s)
                  AND rf2.filing_date < %s
                ORDER BY rf2.filing_date DESC LIMIT 1
                """, (supplier, cust_name, fdate)).fetchone()

            base = {
                "cik": supplier,
                "ticker": tickers.get(supplier),
                "event_date": fdate,
                "accession_no": acc,
                "details": {"customer_name": cust_name, "customer_cik": cust_cik,
                            "pct_of_revenue": float(pct) if pct is not None else None},
            }
            if prev is None:
                events.append({**base, "event_type": "supply_edge_new"})
            else:
                prev_pct = float(prev[0]) if prev[0] is not None else None
                cur_pct = float(pct) if pct is not None else None
                if (prev_pct is not None and cur_pct is not None
                        and abs(cur_pct - prev_pct) >= PCT_MOVE_THRESHOLD):
                    events.append({
                        **base, "event_type": "supply_edge_changed",
                        "details": {**base["details"], "prev_pct": prev_pct,
                                    "delta_pp": round(cur_pct - prev_pct, 2)},
                    })
    return events


def detect_stake_changes(since: dt.date) -> list[dict]:
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT cur.company_cik, cur.filer_cik, cur.filer_name,
                   cur.class_percent, cur.valid_from, cur.accession_no,
                   prev.class_percent AS prev_percent
            FROM analytics.fact_ownership_stake cur
            LEFT JOIN analytics.fact_ownership_stake prev
              ON prev.company_cik = cur.company_cik
             AND prev.filer_cik IS NOT DISTINCT FROM cur.filer_cik
             AND prev.valid_to = cur.valid_from
            WHERE cur.valid_from >= %s AND cur.class_percent IS NOT NULL
            """, (since,)).fetchall()

    tickers = store.ticker_by_cik()
    events: list[dict] = []
    for cik, filer_cik, filer_name, pct, vfrom, acc, prev_pct in rows:
        pct = float(pct)
        prev = float(prev_pct) if prev_pct is not None else None
        crossed = [lvl for lvl in STAKE_LEVELS
                   if (prev is None and pct >= lvl)
                   or (prev is not None and (prev < lvl) != (pct < lvl))]
        moved = prev is not None and abs(pct - prev) >= PCT_MOVE_THRESHOLD
        if not crossed and not moved:
            continue
        events.append({
            "event_type": "stake_change",
            "cik": cik,
            "ticker": tickers.get(cik),
            "event_date": vfrom,
            "accession_no": acc,
            "details": {"filer_name": filer_name, "filer_cik": filer_cik,
                        "class_percent": pct, "prev_percent": prev,
                        "crossed_levels": crossed,
                        "delta_pp": round(pct - prev, 2) if prev is not None else None},
        })
    return events


def detect_insider_clusters(since: dt.date) -> list[dict]:
    """>= CLUSTER_MIN_INSIDERS distinct insiders open-market buying (code P)
    one company within the scan window. Coarse by design: the event key is the
    cluster's latest buy date, so a growing cluster re-emits at most one event
    per new buy date."""
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT company_cik, max(transaction_date) AS event_date,
                   count(DISTINCT person_cik) AS n_insiders,
                   sum(gross_value) AS total_value
            FROM analytics.fact_insider_transaction
            WHERE transaction_code = 'P' AND acquired_disposed = 'A'
              AND transaction_date >= %s
            GROUP BY company_cik
            HAVING count(DISTINCT person_cik) >= %s
            """, (since, CLUSTER_MIN_INSIDERS)).fetchall()

    tickers = store.ticker_by_cik()
    return [{
        "event_type": "insider_cluster",
        "cik": cik,
        "ticker": tickers.get(cik),
        "event_date": event_date,
        "accession_no": None,
        "details": {"n_insiders": n, "total_value": float(v) if v is not None else None,
                    "window_days": (dt.date.today() - since).days},
    } for cik, event_date, n, v in rows]


def run(window_days: int | None = None) -> dict[str, int]:
    """Run every detector over the trailing window. Idempotent."""
    since = _since(window_days)
    out: dict[str, int] = {}
    for name, fn in (
        ("material_8k", detect_material_8k),
        ("supply_edge", detect_supply_edges),
        ("stake_change", detect_stake_changes),
        ("insider_cluster", detect_insider_clusters),
    ):
        try:
            found = fn(since)
            new = store.insert_events(found)
            out[name] = new
            log.info("detect %s: %d candidate(s), %d new since %s",
                     name, len(found), new, since)
        except Exception as exc:  # per-detector isolation
            log.error("detect %s failed: %s", name, exc)
            out[name] = -1
    return out
