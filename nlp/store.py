"""Postgres reads/writes for the supply-chain overlay. Idempotent: a filing that
already has an extract-log row is skipped, so re-runs only do new work."""
from __future__ import annotations

from db.pool import connection
from ingestion import universe as ing_universe


def _supplier_tickers() -> list[str]:
    return ing_universe.supplier_tickers()


def suppliers_to_extract(limit: int) -> list[tuple]:
    """Latest 10-K per in-universe SUPPLIER whose body is stored and which has not
    been extracted yet -> (accession, supplier_cik, ticker, doc_path, filing_date)."""
    with connection(readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT ON (r.cik)
                   r.accession_no, r.cik, c.ticker, r.doc_path, r.filing_date
            FROM raw_filing r
            JOIN dim_company c ON c.cik = r.cik
            WHERE r.form_type = '10-K' AND r.doc_stored AND r.doc_path IS NOT NULL
              AND c.ticker = ANY(%s)
              AND r.accession_no NOT IN (SELECT accession_no FROM supply_extract_log)
            ORDER BY r.cik, r.filing_date DESC
            LIMIT %s
            """,
            (_supplier_tickers(), limit),
        ).fetchall()
    return rows


def companies() -> list[tuple]:
    # (cik, ticker, name, in_universe) from the raw ref table -- available before
    # dbt runs, so extraction can sit upstream of the dbt build in the graph.
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT cik, ticker, title, in_universe FROM dim_company"
        ).fetchall()


def write_relationships(rows: list[tuple]) -> int:
    """rows: (accession, supplier_cik, fiscal_year, name, customer_cik,
    pct, is_named, is_resolved, quote)."""
    if not rows:
        return 0
    with connection() as conn:
        conn.cursor().executemany(
            "INSERT INTO supply_relationship (source_accession, supplier_cik, "
            "fiscal_year, customer_name_raw, customer_cik, pct_of_revenue, "
            "is_named, is_resolved, source_quote) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            rows,
        )
    return len(rows)


def write_log(accession: str, supplier_cik: int, n_facts: int, n_named: int,
              n_resolved: int, model: str, status: str, error: str | None = None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO supply_extract_log (accession_no, supplier_cik, n_facts, "
            "n_named, n_resolved, model, status, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (accession_no) DO UPDATE SET n_facts=EXCLUDED.n_facts, "
            "n_named=EXCLUDED.n_named, n_resolved=EXCLUDED.n_resolved, "
            "status=EXCLUDED.status, error=EXCLUDED.error, extracted_at=now()",
            (accession, supplier_cik, n_facts, n_named, n_resolved, model, status, error),
        )


def quotes_to_embed(limit: int) -> list[tuple]:
    """Distinct (accession, supplier_cik, quote) not yet embedded."""
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT DISTINCT r.source_accession, r.supplier_cik, r.source_quote "
            "FROM supply_relationship r "
            "WHERE r.source_quote <> '' AND NOT EXISTS ("
            "  SELECT 1 FROM supply_embedding e "
            "  WHERE e.accession_no = r.source_accession AND e.quote = r.source_quote) "
            "LIMIT %s",
            (limit,),
        ).fetchall()


def write_embedding(accession: str, supplier_cik: int, quote: str, vec: list[float]) -> None:
    vec_str = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    with connection() as conn:
        conn.execute(
            "INSERT INTO supply_embedding (accession_no, supplier_cik, quote, embedding) "
            "VALUES (%s,%s,%s,%s::vector)",
            (accession, supplier_cik, quote, vec_str),
        )


def counts() -> dict:
    with connection(readonly=True) as conn:
        rels, named, resolved, suppliers, embeds = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE is_named), "
            "count(*) FILTER (WHERE is_resolved), count(DISTINCT supplier_cik), "
            "(SELECT count(*) FROM supply_embedding) "
            "FROM supply_relationship"
        ).fetchone()
    return {"relationships": rels, "named": named, "resolved": resolved,
            "suppliers": suppliers, "embeddings": embeds}


def resolved_edges() -> list[tuple]:
    with connection(readonly=True) as conn:
        return conn.execute(
            "SELECT s.ticker, c.ticker, r.pct_of_revenue, r.customer_name_raw "
            "FROM supply_relationship r "
            "JOIN dim_company s ON s.cik = r.supplier_cik "
            "JOIN dim_company c ON c.cik = r.customer_cik "
            "WHERE r.is_resolved ORDER BY r.pct_of_revenue DESC NULLS LAST"
        ).fetchall()
