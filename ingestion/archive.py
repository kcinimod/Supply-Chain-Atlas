"""Bronze layer: store each fetched document immutably, gzipped, in a
date-partitioned tree. Never overwrites; returns (path, sha256, size)."""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import re

from ingestion import config


def _slug_form(form_type: str) -> str:
    # "SC 13D/A" -> "SC_13D_A"
    return re.sub(r"[^A-Za-z0-9]+", "_", form_type).strip("_")


def write_document(accession: str, cik: int, form_type: str,
                   filing_date: dt.date | str | None, content: bytes,
                   base_dir=None) -> tuple[str, str, int]:
    day = str(filing_date) if filing_date else "unknown"
    root = base_dir or config.RAW_DIR
    dest_dir = root / _slug_form(form_type) / f"dt={day}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{accession}.gz"

    with gzip.open(dest, "wb") as fh:
        fh.write(content)

    sha256 = hashlib.sha256(content).hexdigest()
    return str(dest.relative_to(config.REPO_ROOT)), sha256, len(content)
