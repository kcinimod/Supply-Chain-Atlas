-- Phase 2 (silver): parent -> subsidiary relationships parsed from Exhibit 21 of
-- the 10-K. Subsidiaries are almost always private entities with NO SEC CIK, so
-- they are name-only nodes (the honest resolution limit for this edge). One row
-- per (10-K, subsidiary).

CREATE TABLE IF NOT EXISTS exhibit21_subsidiary (
    accession_no      TEXT    NOT NULL REFERENCES raw_filing(accession_no),
    sub_seq           INT     NOT NULL,   -- order within the exhibit (dedup key)
    parent_cik        BIGINT,             -- the 10-K filer (= raw_filing.cik)
    parent_name       TEXT,
    subsidiary_name   TEXT,
    jurisdiction      TEXT,               -- state / country of organization
    filing_date       DATE,
    parsed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, sub_seq)
);
CREATE INDEX IF NOT EXISTS idx_ex21_parent ON exhibit21_subsidiary (parent_cik);

-- One row per attempted 10-K. status 'no_exhibit' is terminal (many 10-Ks have
-- no Exhibit 21 -- no subsidiaries, or incorporated by reference), so selection
-- skips ok + no_exhibit and only retries 'error'.
CREATE TABLE IF NOT EXISTS exhibit21_parse_log (
    accession_no      TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    subsidiary_count  INT,
    status            TEXT NOT NULL,      -- 'ok' | 'no_exhibit' | 'error'
    error             TEXT,
    doc_name          TEXT,               -- the exhibit filename located
    xml_path          TEXT,
    parsed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
