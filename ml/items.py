"""Build the label layer: pull each universe 8-K's Item codes from the
submissions API and derive the material/routine label.

The submissions JSON carries a parallel `items` array that the Phase-1 ingest
parser dropped (it wasn't needed then). We re-read it here rather than re-fetch
every document, because the codes are metadata, not body text -- ~84 CIK calls,
not thousands of document GETs.
"""
from __future__ import annotations

import logging
from typing import Iterator

from ingestion import config as ing_config
from ingestion import edgar_client, http_client, store as ing_store
from ml import config, store

log = logging.getLogger(__name__)


def _iter_8k_items(cik: int, *, session=None) -> Iterator[dict]:
    """Yield {accession, filing_date, form, items} for every 8-K of a CIK.
    Reads the recent block plus older shard files (same shape as ingest)."""
    def rows(block: dict) -> Iterator[dict]:
        accs = block.get("accessionNumber", [])
        forms = block.get("form", [])
        dates = block.get("filingDate", [])
        items = block.get("items") or [""] * len(accs)
        for i in range(len(accs)):
            if forms[i] in config.TARGET_8K_FORMS:
                yield {"accession": accs[i], "filing_date": dates[i],
                       "form": forms[i], "items": items[i] or ""}

    data = http_client.get_json(edgar_client.submissions_url(cik), session=session)
    filings = data.get("filings", {})
    yield from rows(filings.get("recent", {}))
    for shard in filings.get("files", []):
        block = http_client.get_json(
            edgar_client.submissions_url(cik, shard["name"]), session=session)
        yield from rows(block)


def _parse_codes(items_raw: str) -> list[str]:
    """'5.02,9.01' -> ['5.02', '9.01']; tolerate 'Item 5.02' style noise."""
    codes = []
    for chunk in items_raw.replace(";", ",").split(","):
        tok = chunk.strip().removeprefix("Item").strip()
        if tok and tok[0].isdigit():
            codes.append(tok)
    return codes


def run() -> dict:
    ciks = ing_store.universe_ciks()
    session = http_client.make_session()
    labelled, skipped = [], 0

    for cik in sorted(ciks):
        known = store.existing_8k_accessions({cik})  # FK-safe: only backfilled rows
        try:
            for row in _iter_8k_items(cik, session=session):
                acc = row["accession"]
                if acc not in known:
                    skipped += 1
                    continue
                codes = _parse_codes(row["items"])
                labelled.append((
                    acc, cik, row["filing_date"] or None,
                    row["form"].endswith("/A"), row["items"],
                    codes, config.item_codes_are_material(codes),
                ))
        except Exception as exc:  # isolate per-CIK, keep going
            log.error("items FAILED for CIK %s: %s", cik, exc)

    written = store.upsert_items(labelled)
    counts = store.item_counts()
    log.info("items: labelled %d 8-Ks (%d not-yet-backfilled skipped)", written, skipped)
    return {"written": written, "skipped": skipped, **counts}
