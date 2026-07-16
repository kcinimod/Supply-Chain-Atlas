"""Postgres reads/writes for the Phase 2 transform layer. Loads are idempotent:
transactions upsert on (accession_no, txn_seq); every attempted filing gets a
parse-log row so a filing is never re-parsed and per-item errors are auditable.
"""
from __future__ import annotations

from db.pool import connection

_TXN_COLS = (
    "accession_no", "txn_seq", "is_derivative",
    "issuer_cik", "issuer_name", "issuer_symbol",
    "owner_cik", "owner_name",
    "is_director", "is_officer", "is_ten_pct_owner", "is_other_relation",
    "officer_title", "security_title", "transaction_date", "transaction_code",
    "shares", "price_per_share", "acquired_disposed",
    "shares_owned_following", "direct_indirect", "filing_date",
)


def select_unparsed_form4(limit: int) -> list[tuple]:
    """Universe-issuer Form 4/4-A filings not yet in the parse log.
    raw_filing.cik is the issuer CIK (the filing was crawled under the issuer's
    submissions history), so joining in_universe scopes to our companies."""
    sql = (
        "SELECT r.accession_no, r.cik, r.form_type, r.filing_date, r.primary_doc_url "
        "FROM raw_filing r "
        "JOIN dim_company c ON c.cik = r.cik AND c.in_universe "
        "WHERE r.form_type IN ('4', '4/A') "
        "  AND NOT EXISTS (SELECT 1 FROM form4_parse_log p "
        "                  WHERE p.accession_no = r.accession_no "
        "                    AND p.status = 'ok') "
        "ORDER BY r.filing_date DESC NULLS LAST "
        "LIMIT %s"
    )
    with connection(readonly=True) as conn:
        return list(conn.execute(sql, (limit,)))


def load_transactions(rows: list[dict]) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in _TXN_COLS) for r in rows]
    placeholders = ", ".join(["%s"] * len(_TXN_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _TXN_COLS
                        if c not in ("accession_no", "txn_seq"))
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO form4_transaction ({', '.join(_TXN_COLS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (accession_no, txn_seq) DO UPDATE SET {updates}",
            values,
        )
    return len(values)


def log_parse(accession: str, *, status: str, txn_count: int | None,
              owner_count: int | None, error: str | None,
              xml_path: str | None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO form4_parse_log "
            "  (accession_no, txn_count, owner_count, status, error, xml_path) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no) DO UPDATE SET "
            "  txn_count = EXCLUDED.txn_count, owner_count = EXCLUDED.owner_count, "
            "  status = EXCLUDED.status, error = EXCLUDED.error, "
            "  xml_path = EXCLUDED.xml_path, parsed_at = now()",
            (accession, txn_count, owner_count, status, error, xml_path),
        )


def counts() -> dict[str, int]:
    with connection(readonly=True) as conn:
        txns = conn.execute("SELECT count(*) FROM form4_transaction").fetchone()[0]
        parsed = conn.execute("SELECT count(*) FROM form4_parse_log").fetchone()[0]
        errors = conn.execute(
            "SELECT count(*) FROM form4_parse_log WHERE status = 'error'").fetchone()[0]
    return {"transactions": txns, "filings_parsed": parsed, "errors": errors}


# --- Form 13F --------------------------------------------------------------
_HOLDING_COLS = (
    "accession_no", "holding_seq", "manager_cik", "manager_name",
    "period_of_report", "name_of_issuer", "title_of_class", "cusip",
    "value_reported", "value_usd", "shares", "shares_type",
    "investment_discretion", "voting_sole", "voting_shared", "voting_none",
    "filing_date",
)


def select_unparsed_form13f(limit: int) -> list[tuple]:
    """Universe 13F-HR filings not yet successfully parsed. raw_filing.cik is the
    filing manager's CIK (a 13F is filed by the institution), so joining
    in_universe scopes to our institutional owners."""
    sql = (
        "SELECT r.accession_no, r.cik, r.form_type, r.filing_date, r.primary_doc_url "
        "FROM raw_filing r "
        "JOIN dim_company c ON c.cik = r.cik AND c.in_universe "
        "WHERE r.form_type IN ('13F-HR', '13F-HR/A') "
        "  AND NOT EXISTS (SELECT 1 FROM form13f_parse_log p "
        "                  WHERE p.accession_no = r.accession_no "
        "                    AND p.status = 'ok') "
        "ORDER BY r.filing_date DESC NULLS LAST "
        "LIMIT %s"
    )
    with connection(readonly=True) as conn:
        return list(conn.execute(sql, (limit,)))


def load_holdings(rows: list[dict]) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in _HOLDING_COLS) for r in rows]
    placeholders = ", ".join(["%s"] * len(_HOLDING_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _HOLDING_COLS
                        if c not in ("accession_no", "holding_seq"))
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO form13f_holding ({', '.join(_HOLDING_COLS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (accession_no, holding_seq) DO UPDATE SET {updates}",
            values,
        )
    return len(values)


def log_parse_13f(accession: str, *, status: str, holding_count: int | None,
                  error: str | None, xml_path: str | None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO form13f_parse_log "
            "  (accession_no, holding_count, status, error, xml_path) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no) DO UPDATE SET "
            "  holding_count = EXCLUDED.holding_count, status = EXCLUDED.status, "
            "  error = EXCLUDED.error, xml_path = EXCLUDED.xml_path, parsed_at = now()",
            (accession, holding_count, status, error, xml_path),
        )


def counts_13f() -> dict[str, int]:
    with connection(readonly=True) as conn:
        holdings = conn.execute("SELECT count(*) FROM form13f_holding").fetchone()[0]
        parsed = conn.execute("SELECT count(*) FROM form13f_parse_log").fetchone()[0]
        errors = conn.execute(
            "SELECT count(*) FROM form13f_parse_log WHERE status = 'error'").fetchone()[0]
    return {"holdings": holdings, "filings_parsed": parsed, "errors": errors}


# --- Exhibit 21 ------------------------------------------------------------
_SUB_COLS = (
    "accession_no", "sub_seq", "parent_cik", "parent_name",
    "subsidiary_name", "jurisdiction", "filing_date",
)


def select_unparsed_10k(limit: int) -> list[tuple]:
    """Universe 10-K/10-K-A filings not yet parsed for Exhibit 21. Both 'ok' and
    'no_exhibit' are terminal, so only genuine errors are retried."""
    sql = (
        "SELECT r.accession_no, r.cik, r.form_type, r.filing_date, r.primary_doc_url "
        "FROM raw_filing r "
        "JOIN dim_company c ON c.cik = r.cik AND c.in_universe "
        "WHERE r.form_type IN ('10-K', '10-K/A') "
        "  AND NOT EXISTS (SELECT 1 FROM exhibit21_parse_log p "
        "                  WHERE p.accession_no = r.accession_no "
        "                    AND p.status IN ('ok', 'no_exhibit')) "
        "ORDER BY r.filing_date DESC NULLS LAST "
        "LIMIT %s"
    )
    with connection(readonly=True) as conn:
        return list(conn.execute(sql, (limit,)))


def load_subsidiaries(rows: list[dict]) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in _SUB_COLS) for r in rows]
    placeholders = ", ".join(["%s"] * len(_SUB_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _SUB_COLS
                        if c not in ("accession_no", "sub_seq"))
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO exhibit21_subsidiary ({', '.join(_SUB_COLS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (accession_no, sub_seq) DO UPDATE SET {updates}",
            values,
        )
    return len(values)


def log_parse_ex21(accession: str, *, status: str, subsidiary_count: int | None,
                   error: str | None, doc_name: str | None,
                   xml_path: str | None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO exhibit21_parse_log "
            "  (accession_no, subsidiary_count, status, error, doc_name, xml_path) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no) DO UPDATE SET "
            "  subsidiary_count = EXCLUDED.subsidiary_count, status = EXCLUDED.status, "
            "  error = EXCLUDED.error, doc_name = EXCLUDED.doc_name, "
            "  xml_path = EXCLUDED.xml_path, parsed_at = now()",
            (accession, subsidiary_count, status, error, doc_name, xml_path),
        )


def counts_ex21() -> dict[str, int]:
    with connection(readonly=True) as conn:
        subs = conn.execute("SELECT count(*) FROM exhibit21_subsidiary").fetchone()[0]
        parsed = conn.execute("SELECT count(*) FROM exhibit21_parse_log").fetchone()[0]
        errors = conn.execute(
            "SELECT count(*) FROM exhibit21_parse_log WHERE status = 'error'").fetchone()[0]
        no_ex = conn.execute(
            "SELECT count(*) FROM exhibit21_parse_log WHERE status = 'no_exhibit'").fetchone()[0]
    return {"subsidiaries": subs, "filings_parsed": parsed,
            "errors": errors, "no_exhibit": no_ex}


# --- Schedule 13D/G --------------------------------------------------------
_STAKE_COLS = (
    "accession_no", "stake_seq", "submission_type", "source_format",
    "filer_cik", "filer_name", "subject_cik", "subject_name", "cusip",
    "reporting_person_name", "class_percent", "aggregate_shares",
    "sole_voting", "shared_voting", "sole_dispositive", "shared_dispositive",
    "event_date", "filing_date", "is_amendment",
)


def select_unparsed_13dg(limit: int) -> list[tuple]:
    """Universe 13D/G filings not yet parsed. Operating-company filings (where the
    company is the subject, so the stake lands IN-universe) are ordered first, so a
    bounded batch yields in-universe ownership edges rather than only the owners'
    out-of-universe portfolio filings."""
    sql = (
        "SELECT r.accession_no, r.cik, r.form_type, r.filing_date "
        "FROM raw_filing r "
        "JOIN dim_company c ON c.cik = r.cik AND c.in_universe "
        "WHERE r.form_type IN ('SC 13D', 'SC 13D/A', 'SC 13G', 'SC 13G/A', "
        "                      'SCHEDULE 13D', 'SCHEDULE 13D/A', "
        "                      'SCHEDULE 13G', 'SCHEDULE 13G/A') "
        "  AND NOT EXISTS (SELECT 1 FROM sched13dg_parse_log p "
        "                  WHERE p.accession_no = r.accession_no "
        "                    AND p.status = 'ok') "
        "ORDER BY (c.module = 'owner'), r.filing_date DESC NULLS LAST "
        "LIMIT %s"
    )
    with connection(readonly=True) as conn:
        return list(conn.execute(sql, (limit,)))


def load_stakes(rows: list[dict]) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in _STAKE_COLS) for r in rows]
    placeholders = ", ".join(["%s"] * len(_STAKE_COLS))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in _STAKE_COLS
                        if c not in ("accession_no", "stake_seq"))
    with connection() as conn, conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO sched13dg_stake ({', '.join(_STAKE_COLS)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (accession_no, stake_seq) DO UPDATE SET {updates}",
            values,
        )
    return len(values)


def log_parse_13dg(accession: str, *, status: str, stake_count: int | None,
                   source_format: str | None, error: str | None,
                   xml_path: str | None) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO sched13dg_parse_log "
            "  (accession_no, stake_count, source_format, status, error, xml_path) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (accession_no) DO UPDATE SET "
            "  stake_count = EXCLUDED.stake_count, source_format = EXCLUDED.source_format, "
            "  status = EXCLUDED.status, error = EXCLUDED.error, "
            "  xml_path = EXCLUDED.xml_path, parsed_at = now()",
            (accession, stake_count, source_format, status, error, xml_path),
        )


def counts_13dg() -> dict[str, int]:
    with connection(readonly=True) as conn:
        stakes = conn.execute("SELECT count(*) FROM sched13dg_stake").fetchone()[0]
        parsed = conn.execute("SELECT count(*) FROM sched13dg_parse_log").fetchone()[0]
        errors = conn.execute(
            "SELECT count(*) FROM sched13dg_parse_log WHERE status = 'error'").fetchone()[0]
        xml = conn.execute(
            "SELECT count(*) FROM sched13dg_parse_log WHERE source_format = 'xml'").fetchone()[0]
    return {"stakes": stakes, "filings_parsed": parsed, "errors": errors, "xml": xml}
