-- Phase 1 schema: reference dimension, raw filing landing, ingest checkpoints.
-- pgvector is enabled now so later phases (embeddings) need no migration change.
CREATE EXTENSION IF NOT EXISTS vector;

-- Canonical company reference (seeded from company_tickers.json). The full
-- entity-resolution layer (people, name history, SCD) arrives in Phase 2.
CREATE TABLE IF NOT EXISTS dim_company (
    cik          BIGINT PRIMARY KEY,
    ticker       TEXT,
    title        TEXT,
    source       TEXT,
    in_universe  BOOLEAN NOT NULL DEFAULT FALSE,
    module       TEXT,               -- A_semiconductors / B_datacenter_power / ... / owner
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dim_company_ticker ON dim_company (ticker);
CREATE INDEX IF NOT EXISTS idx_dim_company_universe ON dim_company (in_universe) WHERE in_universe;

-- Immutable landing record for every filing we know about. accession_no is the
-- natural idempotency key: re-ingesting a filing is a no-op.
CREATE TABLE IF NOT EXISTS raw_filing (
    accession_no      TEXT PRIMARY KEY,
    cik               BIGINT NOT NULL,
    company_name      TEXT,
    form_type         TEXT NOT NULL,
    filing_date       DATE,
    primary_doc_url   TEXT,
    doc_stored        BOOLEAN NOT NULL DEFAULT FALSE,
    doc_path          TEXT,
    doc_sha256        TEXT,
    doc_bytes         BIGINT,
    source_index_date DATE,           -- set by the poller (which daily index it came from)
    fetched_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_amendment      BOOLEAN NOT NULL DEFAULT FALSE,
    amends_accession  TEXT            -- resolved from the document in Phase 2
);
CREATE INDEX IF NOT EXISTS idx_raw_filing_cik ON raw_filing (cik);
CREATE INDEX IF NOT EXISTS idx_raw_filing_form ON raw_filing (form_type);
CREATE INDEX IF NOT EXISTS idx_raw_filing_date ON raw_filing (filing_date);
CREATE INDEX IF NOT EXISTS idx_raw_filing_nodoc ON raw_filing (doc_stored) WHERE NOT doc_stored;

-- Checkpoint / resume state, one row per ingestion source.
CREATE TABLE IF NOT EXISTS ingest_checkpoint (
    source       TEXT PRIMARY KEY,
    cursor_value TEXT,
    last_status  TEXT,
    records      BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
