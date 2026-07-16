-- Phase 2 (silver): parsed Form 4 insider transactions + a parse audit log.
-- Python owns this landing layer (messy XML -> typed rows); dbt reads it as a
-- source and builds the gold star schema on top. One row per transaction.

CREATE TABLE IF NOT EXISTS form4_transaction (
    accession_no            TEXT    NOT NULL REFERENCES raw_filing(accession_no),
    txn_seq                 INT     NOT NULL,   -- order within the filing (dedup key)
    is_derivative           BOOLEAN NOT NULL,
    -- issuer (the company the insider transacted in)
    issuer_cik              BIGINT,
    issuer_name             TEXT,
    issuer_symbol           TEXT,
    -- reporting owner (the insider); rptOwnerCik makes person resolution partly free
    owner_cik               BIGINT,
    owner_name              TEXT,
    is_director             BOOLEAN,
    is_officer              BOOLEAN,
    is_ten_pct_owner        BOOLEAN,
    is_other_relation       BOOLEAN,
    officer_title           TEXT,
    -- the transaction itself
    security_title          TEXT,
    transaction_date        DATE,
    transaction_code        TEXT,               -- A=acquire, S=sale, M=exercise, ...
    shares                  NUMERIC,
    price_per_share         NUMERIC,
    acquired_disposed       TEXT,               -- 'A' or 'D'
    shares_owned_following  NUMERIC,
    direct_indirect         TEXT,               -- 'D' direct / 'I' indirect
    filing_date             DATE,
    parsed_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, txn_seq)
);
CREATE INDEX IF NOT EXISTS idx_form4_owner  ON form4_transaction (owner_cik);
CREATE INDEX IF NOT EXISTS idx_form4_issuer ON form4_transaction (issuer_cik);
CREATE INDEX IF NOT EXISTS idx_form4_date   ON form4_transaction (transaction_date);

-- One row per filing we attempted to parse. Records even zero-transaction
-- filings (holdings-only) and errors, so selection stays idempotent (never
-- re-parses a done filing) and per-item failures are auditable.
CREATE TABLE IF NOT EXISTS form4_parse_log (
    accession_no    TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    txn_count       INT,
    owner_count     INT,
    status          TEXT NOT NULL,              -- 'ok' | 'error'
    error           TEXT,
    xml_path        TEXT,
    parsed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
