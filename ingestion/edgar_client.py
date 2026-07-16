"""EDGAR-specific URLs and parsing. No DB, no side effects beyond HTTP GETs.

Two ingestion sources:
* submissions API (data.sec.gov)  -> a company's full filing history (backfill)
* daily-index master.idx (sec.gov) -> everything filed on a given day (poller)
"""
from __future__ import annotations

import datetime as dt
from typing import Iterator

from ingestion import config, http_client


# --- submissions API (per-company history) ---------------------------------
def submissions_url(cik: int, name: str | None = None) -> str:
    if name:  # older-filing shards are referenced by bare filename
        return f"{config.DATA_SEC_BASE}/submissions/{name}"
    return f"{config.DATA_SEC_BASE}/submissions/CIK{cik:010d}.json"


def _rows_from_block(block: dict) -> Iterator[dict]:
    """A filings block is parallel arrays (accessionNumber, form, ...)."""
    accs = block.get("accessionNumber", [])
    for i in range(len(accs)):
        yield {
            "accession": accs[i],
            "form": block["form"][i],
            "filing_date": block["filingDate"][i],
            "primary_document": (block.get("primaryDocument") or [None] * len(accs))[i],
        }


def iter_company_filings(cik: int, *, session=None) -> Iterator[dict]:
    """Yield every filing for a CIK: the recent block plus older shard files."""
    data = http_client.get_json(submissions_url(cik), session=session)
    filings = data.get("filings", {})
    yield from _rows_from_block(filings.get("recent", {}))
    for shard in filings.get("files", []):
        block = http_client.get_json(submissions_url(cik, shard["name"]), session=session)
        yield from _rows_from_block(block)


def company_name_from_submissions(cik: int, *, session=None) -> str | None:
    data = http_client.get_json(submissions_url(cik), session=session)
    return data.get("name")


# --- daily index (all filers on one day) -----------------------------------
def daily_index_url(day: dt.date) -> str:
    q = (day.month - 1) // 3 + 1
    return (f"{config.SEC_BASE}/Archives/edgar/daily-index/"
            f"{day.year}/QTR{q}/master.{day:%Y%m%d}.idx")


def fetch_daily_index(day: dt.date, *, session=None) -> list[dict]:
    """Parse master.idx: 'CIK|Company|Form Type|Date Filed|Filename'.
    Returns [] when the day has no index (weekend/holiday)."""
    # SEC Archives return 403 (not 404) for a not-yet-published / missing day.
    text = http_client.get_text(daily_index_url(day), session=session,
                                allow_missing={403, 404})
    if text is None:
        return []
    rows = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].isdigit():
            continue  # skip header / preamble lines
        cik, company, form, date_filed, filename = parts
        rows.append({
            "cik": int(cik),
            "company": company,
            "form": form,
            "filing_date": date_filed,
            "accession": accession_from_filename(filename),
        })
    return rows


# --- document URLs ---------------------------------------------------------
def accession_from_filename(filename: str) -> str:
    # 'edgar/data/320193/0000320193-24-000123.txt' -> '0000320193-24-000123'
    return filename.rsplit("/", 1)[-1].removesuffix(".txt")


def primary_doc_url(cik: int, accession: str, primary_document: str | None) -> str | None:
    if not primary_document:
        return None
    nodash = accession.replace("-", "")
    return f"{config.SEC_BASE}/Archives/edgar/data/{cik}/{nodash}/{primary_document}"


def full_submission_url(cik: int, accession: str) -> str:
    return f"{config.SEC_BASE}/Archives/edgar/data/{cik}/{accession}.txt"
