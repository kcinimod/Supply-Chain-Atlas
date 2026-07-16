"""Download the actual filing documents to the bronze layer, bounded per run.
Uses the primary document when known, else the full submission .txt. Per-item
error isolation; re-running only fetches what's still missing (idempotent)."""
from __future__ import annotations

import logging

from ingestion import archive, config, edgar_client, http_client, store

log = logging.getLogger(__name__)


def run(limit: int | None = None, form_prefix: str | None = None) -> int:
    limit = limit or config.DOC_FETCH_LIMIT
    todo = store.filings_missing_docs(limit, form_prefix)

    stored, failures = 0, 0
    for accession, cik, form_type, filing_date, primary_url in todo:
        url = primary_url or edgar_client.full_submission_url(cik, accession)
        try:
            content = http_client.get_bytes(url)
            path, sha, nbytes = archive.write_document(
                accession, cik, form_type, filing_date, content)
            store.mark_doc_stored(accession, path, sha, nbytes)
            stored += 1
        except Exception as exc:  # isolate: keep fetching the rest
            failures += 1
            log.error("fetch-docs FAILED %s (%s): %s", accession, url, exc)

    log.info("fetch-docs done: %d stored, %d failures (of %d attempted)",
             stored, failures, len(todo))
    return stored
