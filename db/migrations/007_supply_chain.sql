-- Phase 6 (the namesake overlay): supplier -> customer edges from 10-K free text.
--
-- Unlike every prior edge, this one lives ONLY in prose -- the 10-K customer-
-- concentration disclosure ("customer X accounted for 14% of revenue"). A local
-- LLM extracts the facts; extraction is inherently PARTIAL (many filers name no
-- customer, or anonymise it as "Customer A"), so the schema records the honest
-- reliability tiers: named vs unnamed, resolved-to-CIK vs name-only.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS supply_relationship (
    rel_id            BIGSERIAL PRIMARY KEY,
    source_accession  TEXT   NOT NULL REFERENCES raw_filing(accession_no),
    supplier_cik      BIGINT NOT NULL,       -- the disclosing filer (10-K)
    fiscal_year       INT,
    customer_name_raw TEXT   NOT NULL,       -- as extracted ("NVIDIA", "one customer", "Customer A")
    customer_cik      BIGINT,                -- resolved in-universe CIK, else NULL
    pct_of_revenue    NUMERIC,               -- % of the supplier's revenue, else NULL
    is_named          BOOLEAN NOT NULL,      -- a real company name (not "one customer"/"Customer A")
    is_resolved       BOOLEAN NOT NULL,      -- customer_cik matched an in-universe company
    source_quote      TEXT,                  -- provenance: the sentence it came from
    extracted_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_supply_supplier ON supply_relationship (supplier_cik);
CREATE INDEX IF NOT EXISTS idx_supply_customer ON supply_relationship (customer_cik);

CREATE TABLE IF NOT EXISTS supply_extract_log (
    accession_no  TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    supplier_cik  BIGINT,
    n_facts       INT,
    n_named       INT,
    n_resolved    INT,
    model         TEXT,
    status        TEXT NOT NULL,             -- 'ok' | 'no_passage' | 'error'
    error         TEXT,
    extracted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Disclosure passages embedded locally (nomic-embed-text, 768-dim) -> semantic
-- search over supply-chain language + the Day-5 GPU/embeddings story.
CREATE TABLE IF NOT EXISTS supply_embedding (
    emb_id        BIGSERIAL PRIMARY KEY,
    accession_no  TEXT   NOT NULL REFERENCES raw_filing(accession_no),
    supplier_cik  BIGINT NOT NULL,
    quote         TEXT   NOT NULL,
    embedding     VECTOR(768) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
