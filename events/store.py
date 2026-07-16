"""Postgres reads/writes for the event & alert layer. Same discipline as every
other layer: natural-key upserts, so detectors and rules re-run as no-ops."""
from __future__ import annotations

import json
import datetime as dt

from db.pool import connection


def insert_events(rows: list[dict]) -> int:
    """rows: {event_type, cik, ticker, event_date, accession_no, details}.
    Returns the number of NEW events (conflicts = already detected)."""
    if not rows:
        return 0
    new = 0
    with connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "INSERT INTO filing_event "
                    "  (event_type, cik, ticker, event_date, accession_no, details) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (event_type, cik, event_date, accession_no) DO NOTHING",
                    (r["event_type"], r["cik"], r.get("ticker"), r["event_date"],
                     r.get("accession_no"), json.dumps(r.get("details", {}), default=str)),
                )
                new += cur.rowcount
    return new


def events_without_alerts() -> list[dict]:
    """Events no alert has been derived from yet (the rule engine's work queue)."""
    cols = ("event_id", "event_type", "cik", "ticker", "event_date",
            "accession_no", "details")
    with connection(readonly=True) as conn:
        rows = conn.execute(
            "SELECT e.event_id, e.event_type, e.cik, e.ticker, e.event_date, "
            "       e.accession_no, e.details "
            "FROM filing_event e "
            "LEFT JOIN alert a ON a.event_id = e.event_id "
            "WHERE a.alert_id IS NULL "
            "ORDER BY e.event_date, e.event_id").fetchall()
    return [dict(zip(cols, r)) for r in rows]


def insert_alerts(rows: list[dict]) -> int:
    """rows: {rule, severity, cik, ticker, title, body, event_id, source,
    dedup_key}. Returns NEW alerts (dedup_key conflicts skipped)."""
    if not rows:
        return 0
    new = 0
    with connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "INSERT INTO alert "
                    "  (rule, severity, cik, ticker, title, body, event_id, source, dedup_key) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (dedup_key) DO NOTHING",
                    (r["rule"], r["severity"], r.get("cik"), r.get("ticker"),
                     r["title"], r.get("body"), r.get("event_id"),
                     r.get("source", "batch"), r["dedup_key"]),
                )
                new += cur.rowcount
    return new


def ticker_by_cik() -> dict[int, str]:
    """Universe CIK -> canonical ticker (for tagging events with a tradeable)."""
    with connection(readonly=True) as conn:
        return {cik: t for cik, t in conn.execute(
            "SELECT cik, ticker FROM dim_company "
            "WHERE in_universe AND ticker IS NOT NULL")}


def title_by_cik(ciks: list[int]) -> dict[int, str]:
    if not ciks:
        return {}
    with connection(readonly=True) as conn:
        return {cik: t for cik, t in conn.execute(
            "SELECT cik, coalesce(title, ticker, cik::text) FROM dim_company "
            "WHERE cik = ANY(%s)", (ciks,))}


def counts() -> dict[str, int]:
    with connection(readonly=True) as conn:
        by_type = dict(conn.execute(
            "SELECT event_type, count(*) FROM filing_event GROUP BY event_type"))
        n_alerts = conn.execute("SELECT count(*) FROM alert").fetchone()[0]
    return {"events_by_type": by_type, "alerts": n_alerts}


def active_horizons() -> list[tuple[str, int]]:
    with connection(readonly=True) as conn:
        return list(conn.execute(
            "SELECT horizon, trading_days FROM reaction_horizon "
            "WHERE active ORDER BY trading_days"))
