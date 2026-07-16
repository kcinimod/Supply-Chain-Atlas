"""All Postgres reads/writes for the ML spine. Upserts keep every step
idempotent, matching the rest of the pipeline."""
from __future__ import annotations

from db.pool import connection
from ml import config


# --- label layer (eightk_item) ---------------------------------------------
def existing_8k_accessions(ciks: set[int]) -> set[str]:
    """8-K accessions already in raw_filing for these CIKs (FK-safe insert set)."""
    if not ciks:
        return set()
    with connection(readonly=True) as conn:
        rows = conn.execute(
            "SELECT accession_no FROM raw_filing "
            "WHERE cik = ANY(%s) AND form_type = ANY(%s)",
            (list(ciks), list(config.TARGET_8K_FORMS)),
        ).fetchall()
    return {r[0] for r in rows}


def upsert_items(rows: list[tuple]) -> int:
    """rows: (accession, cik, filing_date, is_amendment, items_raw, item_codes, is_material)."""
    if not rows:
        return 0
    with connection() as conn:
        conn.cursor().executemany(
            "INSERT INTO eightk_item "
            "  (accession_no, cik, filing_date, is_amendment, items_raw, item_codes, is_material) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no) DO UPDATE SET "
            "  items_raw = EXCLUDED.items_raw, item_codes = EXCLUDED.item_codes, "
            "  is_material = EXCLUDED.is_material, labeled_at = now()",
            rows,
        )
    return len(rows)


def item_counts() -> dict:
    with connection(readonly=True) as conn:
        total, material, routine, unknown = conn.execute(
            "SELECT count(*), "
            "  count(*) FILTER (WHERE is_material), "
            "  count(*) FILTER (WHERE is_material IS FALSE), "
            "  count(*) FILTER (WHERE is_material IS NULL) "
            "FROM eightk_item"
        ).fetchone()
    return {"total": total, "material": material, "routine": routine, "unknown": unknown}


def labeled_with_docs() -> list[tuple]:
    """(accession, doc_path, is_material, filing_date) for labelled 8-Ks whose
    body is on disk -- the training/eval universe. Ordered by filing_date so a
    time-based holdout is a simple tail slice."""
    with connection(readonly=True) as conn:
        rows = conn.execute(
            "SELECT i.accession_no, r.doc_path, i.is_material, i.filing_date "
            "FROM eightk_item i JOIN raw_filing r USING (accession_no) "
            "WHERE i.is_material IS NOT NULL AND r.doc_stored AND r.doc_path IS NOT NULL "
            "ORDER BY i.filing_date, i.accession_no"
        ).fetchall()
    return rows


# --- predictions & metrics --------------------------------------------------
def write_predictions(model_version: str, rows: list[tuple]) -> int:
    """rows: (accession, proba, predicted, label)."""
    if not rows:
        return 0
    with connection() as conn:
        conn.cursor().executemany(
            "INSERT INTO eightk_prediction "
            "  (accession_no, model_version, proba, predicted, label) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no, model_version) DO UPDATE SET "
            "  proba = EXCLUDED.proba, predicted = EXCLUDED.predicted, "
            "  label = EXCLUDED.label, scored_at = now()",
            [(a, model_version, p, pred, lab) for (a, p, pred, lab) in rows],
        )
    return len(rows)


def unscored_8ks(model_version: str, limit: int = 500) -> list[tuple]:
    """(accession, doc_path, is_material) for stored 8-K bodies this model
    version has not scored yet — the production scoring queue."""
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT i.accession_no, r.doc_path, i.is_material "
            "FROM eightk_item i JOIN raw_filing r USING (accession_no) "
            "WHERE r.doc_stored AND r.doc_path IS NOT NULL "
            "  AND NOT EXISTS (SELECT 1 FROM eightk_prediction p "
            "                  WHERE p.accession_no = i.accession_no "
            "                    AND p.model_version = %s) "
            "ORDER BY i.filing_date DESC LIMIT %s",
            (model_version, limit),
        ).fetchall()


def recent_docs(limit: int) -> list[tuple]:
    """(accession, doc_path) for the most recent stored 8-K bodies — the
    bounded slice the drift check scores (never the whole corpus)."""
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT i.accession_no, r.doc_path "
            "FROM eightk_item i JOIN raw_filing r USING (accession_no) "
            "WHERE r.doc_stored AND r.doc_path IS NOT NULL "
            "ORDER BY i.filing_date DESC LIMIT %s",
            (limit,),
        ).fetchall()


def write_metrics(model_version: str, split: str, metrics: dict) -> None:
    rows = [(model_version, k, float(v), split) for k, v in metrics.items()]
    with connection() as conn:
        conn.cursor().executemany(
            "INSERT INTO eightk_eval_metric (model_version, metric, value, eval_split) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
            rows,
        )


def latest_metrics(split: str = "test") -> list[tuple]:
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT model_version, metric, value, evaluated_at "
            "FROM eightk_eval_metric WHERE eval_split = %s "
            "ORDER BY evaluated_at DESC, metric LIMIT 20",
            (split,),
        ).fetchall()
