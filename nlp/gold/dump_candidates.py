"""Build a model-assisted gold-set review file for the supply-chain extractor.

Emits one JSON object per supplier 10-K to `supply_gold_review.jsonl`:
  - `passages`: the retrieved concentration passages (exactly what the model
    reads) -- this is what the human labels from, not the 400k-char 10-K, and
  - `gold`: PRE-FILLED with the union of every model variant's extractions
    (qwen / llama / improved-prompt), each row tagged with which models proposed
    it. The human curates this list into the truth: delete false rows, fix the
    pct/named fields, and add any customer all models missed.

Model-assisted labeling is ~3-4x faster than from scratch, and the rows where
the models DISAGREE (e.g. UCTT 58.7% vs 11.1%) are exactly the high-value cases
to adjudicate. When done, save the curated file as `supply_gold.jsonl` and run
`python -m nlp.gold.score`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from db.pool import connection
from nlp import resolve, text

log = logging.getLogger("nlp.gold.dump")

OUT = Path(__file__).with_name("supply_gold_review.jsonl")

# Each model variant's extractions live in its own table (created during the A/B).
# Only those that actually exist are used, so this still runs on a fresh clone.
MODEL_TABLES = {
    "qwen_orig": "supply_relationship_qwen",
    "llama": "supply_relationship_llama",
    "qwen_impr": "supply_relationship",   # current live table = improved-prompt Qwen
}


def _supplier_tickers() -> list[str]:
    from ingestion import universe as ing_universe
    return ing_universe.supplier_tickers()


def _existing_tables(conn) -> dict[str, str]:
    present = {r[0] for r in conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()}
    return {tag: tbl for tag, tbl in MODEL_TABLES.items() if tbl in present}


def _supplier_filings(conn) -> list[tuple]:
    """Latest stored 10-K per in-universe supplier (NO log filter -- we want them
    all, including filings already extracted)."""
    return conn.execute(
        """
        SELECT DISTINCT ON (r.cik)
               r.accession_no, r.cik, c.ticker, r.doc_path, r.filing_date
        FROM raw_filing r JOIN dim_company c ON c.cik = r.cik
        WHERE r.form_type = '10-K' AND r.doc_stored AND r.doc_path IS NOT NULL
          AND c.ticker = ANY(%s)
        ORDER BY r.cik, r.filing_date DESC
        """,
        (_supplier_tickers(),),
    ).fetchall()


def _candidates(conn, tables: dict[str, str], accession: str) -> list[dict]:
    """Union of all model extractions for one filing, deduped by (norm-name, pct)."""
    merged: dict[tuple, dict] = {}
    for tag, tbl in tables.items():
        rows = conn.execute(
            f"SELECT customer_name_raw, pct_of_revenue, is_named "
            f"FROM {tbl} WHERE source_accession = %s",   # tbl is an internal constant
            (accession,),
        ).fetchall()
        for name, pct, is_named in rows:
            pct_f = float(pct) if pct is not None else None
            key = (resolve.normalize(name), pct_f)
            if key not in merged:
                merged[key] = {"customer": name, "pct": pct_f,
                               "named": bool(is_named), "proposed_by": []}
            merged[key]["proposed_by"].append(tag)
    # named first, then by descending pct -- easiest to eyeball
    return sorted(merged.values(),
                  key=lambda c: (not c["named"], -(c["pct"] or 0)))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with connection(readonly=True) as conn:
        tables = _existing_tables(conn)
        if not tables:
            raise SystemExit("no model tables found -- run `nlp extract` first")
        log.info("sourcing candidates from: %s", ", ".join(tables.values()))
        filings = _supplier_filings(conn)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with OUT.open("w", encoding="utf-8") as fh:
            for accession, cik, ticker, doc_path, filing_date in filings:
                try:
                    passages = text.concentration_passages(text.read_text(doc_path))
                except Exception as exc:               # a bad doc shouldn't sink the dump
                    log.warning("passages failed for %s: %s", ticker, exc)
                    passages = []
                obj = {
                    "accession": accession,
                    "supplier_ticker": ticker,
                    "supplier_cik": cik,
                    "fiscal_year": filing_date.year if filing_date else None,
                    # >>> HUMAN edits this into the truth. Keep {customer, pct, named};
                    #     delete false rows, fix pct/named, add missed customers.
                    #     `proposed_by` is a reference hint -- ignored by the scorer.
                    "gold": _candidates(conn, tables, accession),
                    "passages": passages,              # read these to label; not scored
                }
                fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
                n += 1

    log.info("wrote %d filings -> %s", n, OUT)
    print(f"\nReview file: {OUT}\n"
          f"1. Edit each line's `gold` list into the ground truth.\n"
          f"2. Save it as supply_gold.jsonl (same folder).\n"
          f"3. Score:  uv run python -m nlp.gold.score [--model qwen_orig|llama|qwen_impr]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
