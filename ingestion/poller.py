"""Incremental/CDC path: read the daily-index files since the last cursor,
keep only filings from universe CIKs + target forms, and upsert. The cursor
(last processed index date) makes it resumable and no-op on a caught-up day."""
from __future__ import annotations

import datetime as dt
import logging

from ingestion import config, edgar_client, store

log = logging.getLogger(__name__)

# EDGAR's electronic full-text index effectively starts here; a sane floor for a
# first poll if no cursor exists yet.
_DEFAULT_LOOKBACK_DAYS = 3


def _start_date() -> dt.date:
    cursor = store.get_checkpoint("poller")
    if cursor:
        return dt.date.fromisoformat(cursor) + dt.timedelta(days=1)
    return dt.date.today() - dt.timedelta(days=_DEFAULT_LOOKBACK_DAYS)


def run(since: str | None = None) -> int:
    universe = store.universe_ciks()
    if not universe:
        log.warning("universe is empty; run load-companies first")
        return 0

    start = dt.date.fromisoformat(since) if since else _start_date()
    today = dt.date.today()
    if start > today:
        log.info("poller: already caught up (next date %s)", start)
        return 0

    total_new, last_done = 0, None
    day = start
    while day <= today:
        index = edgar_client.fetch_daily_index(day)  # [] on weekends/holidays
        rows = [{
            "accession_no": r["accession"],
            "cik": r["cik"],
            "company_name": r["company"],
            "form_type": r["form"],
            "filing_date": r["filing_date"],
            "primary_doc_url": None,
            "source_index_date": day.isoformat(),
            "is_amendment": r["form"].endswith("/A"),
        } for r in index
            if r["cik"] in universe and r["form"] in config.TARGET_FORMS]

        new = store.upsert_filings(rows)
        total_new += new
        if index:
            log.info("poller %s: %d universe filings (%d new)", day, len(rows), new)
        last_done = day
        day += dt.timedelta(days=1)

    if last_done:
        store.set_checkpoint("poller", last_done.isoformat(), "ok", total_new)
    log.info("poller done: %s..%s, %d new filings", start, today, total_new)
    return total_new
