"""History bootstrap: pull each universe company's full filing history from the
submissions API, filter to target forms, and upsert metadata. Per-company error
isolation means one bad entity never kills the run."""
from __future__ import annotations

import datetime as dt
import logging

from ingestion import config, edgar_client, store

log = logging.getLogger(__name__)


def _rows_for_cik(cik: int, ticker: str | None) -> list[dict]:
    name = edgar_client.company_name_from_submissions(cik) or ticker
    rows = []
    for f in edgar_client.iter_company_filings(cik):
        if f["form"] not in config.TARGET_FORMS:
            continue
        rows.append({
            "accession_no": f["accession"],
            "cik": cik,
            "company_name": name,
            "form_type": f["form"],
            "filing_date": f["filing_date"] or None,
            "primary_doc_url": edgar_client.primary_doc_url(
                cik, f["accession"], f["primary_document"]),
            "source_index_date": None,
            "is_amendment": f["form"].endswith("/A"),
        })
    return rows


def run(limit: int | None = None) -> int:
    members = store.universe_members()
    if limit:
        members = members[:limit]

    total_new, total_seen, failures = 0, 0, 0
    for cik, ticker in members:
        try:
            rows = _rows_for_cik(cik, ticker)
            new = store.upsert_filings(rows)
            total_new += new
            total_seen += len(rows)
            log.info("backfill %-6s CIK %-10d %4d filings (%d new)",
                     ticker or "-", cik, len(rows), new)
        except Exception as exc:  # isolate: keep crawling the rest
            failures += 1
            log.error("backfill FAILED for CIK %d (%s): %s", cik, ticker, exc)

    status = "ok" if failures == 0 else f"{failures} failures"
    store.set_checkpoint("backfill", dt.date.today().isoformat(), status, total_seen)
    log.info("backfill done: %d companies, %d filings seen, %d new, %d failures",
             len(members), total_seen, total_new, failures)
    return total_new
