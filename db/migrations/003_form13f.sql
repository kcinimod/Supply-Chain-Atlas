-- Phase 2 (silver): parsed 13F-HR institutional holdings + a parse audit log.
-- A 13F is filed quarterly by an institutional manager and lists every security
-- it holds. One row per holding. Securities are keyed by CUSIP; mapping CUSIP to
-- a company CIK has no free authoritative source, so resolution is deferred (a
-- dim_security keyed on CUSIP is built downstream).

CREATE TABLE IF NOT EXISTS form13f_holding (
    accession_no          TEXT    NOT NULL REFERENCES raw_filing(accession_no),
    holding_seq           INT     NOT NULL,   -- order within the filing (dedup key)
    manager_cik           BIGINT,             -- the filing institution (= raw_filing.cik)
    manager_name          TEXT,
    period_of_report      DATE,               -- quarter-end the holdings are "as of"
    name_of_issuer        TEXT,
    title_of_class        TEXT,
    cusip                 TEXT,
    value_reported        NUMERIC,            -- value as filed
    value_usd             NUMERIC,            -- normalized to whole dollars (see note)
    shares                NUMERIC,
    shares_type           TEXT,               -- 'SH' shares / 'PRN' principal
    investment_discretion TEXT,
    voting_sole           NUMERIC,
    voting_shared         NUMERIC,
    voting_none           NUMERIC,
    filing_date           DATE,
    parsed_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, holding_seq)
);
CREATE INDEX IF NOT EXISTS idx_13f_manager ON form13f_holding (manager_cik);
CREATE INDEX IF NOT EXISTS idx_13f_cusip   ON form13f_holding (cusip);
CREATE INDEX IF NOT EXISTS idx_13f_period  ON form13f_holding (period_of_report);

-- NOTE on value units: SEC reported 13F `value` in THOUSANDS of dollars until the
-- 2022-Q4 report (filings on/after 2023-01-03), then in WHOLE dollars. The parser
-- normalizes to whole dollars in value_usd using the filing date; value_reported
-- keeps the raw figure.

CREATE TABLE IF NOT EXISTS form13f_parse_log (
    accession_no   TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    holding_count  INT,
    status         TEXT NOT NULL,             -- 'ok' | 'error'
    error          TEXT,
    xml_path       TEXT,
    parsed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
