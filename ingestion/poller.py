"""Incremental/CDC path: read the daily-index files since the last cursor,
keep only filings from universe CIKs + target forms, and upsert. The cursor
(last processed index date) makes it resumable and no-op on a caught-up day.

CURSOR SAFETY (the bug this module lost 146 filings to)
------------------------------------------------------
SEC returns 403 -- not 404 -- for a daily index that does not exist, and it
publishes day D's index only after that day's close (~02:00 UTC on D+1). The
scheduled run fires at 01:30 UTC, so asking for "today" always 403s. The
original code read that empty result as "day complete, 0 filings" and advanced
the cursor onto it, so the next run started at D+1 and the real index for D --
published a half hour later -- was never read again. Every day quietly ate
itself: 2026-07-10..07-23 lost 146 universe filings (27 of them 8-Ks) while the
checkpoint kept reporting status 'ok'.

A 403 is therefore ambiguous and must never be treated as proof of an empty
day. We resolve it by DATE instead of by status code:

* weekend  -> no index is ever published; safe to skip and advance.
* weekday, empty, still inside the publish grace window -> unknowable (not
  published yet, or we were rate-limited). STOP the walk and leave the cursor
  behind it, so the next run retries the day.
* weekday, empty, older than the grace window -> a genuine non-filing day (US
  market holiday). Skip it and advance, otherwise the poller stalls forever.

The result is a poller that self-heals: a transient block or an early run costs
a retry, never data.
"""
from __future__ import annotations

import datetime as dt
import logging

from ingestion import config, edgar_client, store

log = logging.getLogger(__name__)

# EDGAR's electronic full-text index effectively starts here; a sane floor for a
# first poll if no cursor exists yet.
_DEFAULT_LOOKBACK_DAYS = 3

# How long to keep retrying an empty weekday index before accepting it as a real
# holiday. Must exceed the lag between the scheduled run and SEC's publish time.
PUBLISH_GRACE_DAYS = 2


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
        index = edgar_client.fetch_daily_index(day)  # [] on weekend/holiday/unpublished
        if not index and day.weekday() < 5 and (today - day).days < PUBLISH_GRACE_DAYS:
            # Weekday with no readable index, still fresh enough that SEC may
            # simply not have published it yet (or we were throttled). Stop here
            # WITHOUT recording this day, so the next run picks it up again.
            log.info("poller %s: index not available yet; stopping so it is retried", day)
            break
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
