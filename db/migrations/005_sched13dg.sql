-- Phase 2 (silver): beneficial-ownership stakes from Schedule 13D/13G.
-- One row per (filing, reporting person). The subject company and filer CIKs come
-- from structured metadata in BOTH eras (the XML fields post-2024-12-18, and the
-- SGML SEC-HEADER before that), so the owner->subject edge always resolves; only
-- the % of class / share figures differ (XML fields vs cover-page text regex).

CREATE TABLE IF NOT EXISTS sched13dg_stake (
    accession_no          TEXT    NOT NULL REFERENCES raw_filing(accession_no),
    stake_seq             INT     NOT NULL,   -- reporting person index (dedup key)
    submission_type       TEXT,
    source_format         TEXT,               -- 'xml' (structured) | 'text' (legacy)
    filer_cik             BIGINT,             -- the beneficial owner filing
    filer_name            TEXT,
    subject_cik           BIGINT,             -- the company being reported on
    subject_name          TEXT,
    cusip                 TEXT,
    reporting_person_name TEXT,
    class_percent         NUMERIC,            -- % of class beneficially owned
    aggregate_shares      NUMERIC,
    sole_voting           NUMERIC,
    shared_voting         NUMERIC,
    sole_dispositive      NUMERIC,
    shared_dispositive    NUMERIC,
    event_date            DATE,               -- date of event requiring filing
    filing_date           DATE,
    is_amendment          BOOLEAN,
    parsed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, stake_seq)
);
CREATE INDEX IF NOT EXISTS idx_13dg_subject ON sched13dg_stake (subject_cik);
CREATE INDEX IF NOT EXISTS idx_13dg_filer   ON sched13dg_stake (filer_cik);
CREATE INDEX IF NOT EXISTS idx_13dg_event   ON sched13dg_stake (event_date);

CREATE TABLE IF NOT EXISTS sched13dg_parse_log (
    accession_no   TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    stake_count    INT,
    source_format  TEXT,
    status         TEXT NOT NULL,             -- 'ok' | 'error'
    error          TEXT,
    xml_path       TEXT,
    parsed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
