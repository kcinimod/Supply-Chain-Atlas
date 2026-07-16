-- Phase 5 (ML spine): the 8-K "substance" classifier.
--
-- Three tables, one per stage of the batch ML lifecycle:
--   eightk_item        the LABEL layer -- Item codes pulled from the submissions
--                      API, plus the derived material/routine label. Text (the
--                      FEATURES) lives in the bronze docs on disk, deliberately
--                      separate so the model learns materiality from LANGUAGE,
--                      not from the item code that defines the label.
--   eightk_prediction  per-filing scored output of a registered model version
--                      (kept so drift / eval run over real serving history).
--   eightk_eval_metric one row per (model version, metric) at eval time -- the
--                      time series that the daily eval asset appends to and the
--                      drift asset-check reads.

CREATE TABLE IF NOT EXISTS eightk_item (
    accession_no   TEXT PRIMARY KEY REFERENCES raw_filing(accession_no),
    cik            BIGINT NOT NULL,
    filing_date    DATE,
    is_amendment   BOOLEAN NOT NULL DEFAULT FALSE,
    items_raw      TEXT,                 -- comma-joined item codes as EDGAR gives them
    item_codes     TEXT[] NOT NULL,      -- normalised ['1.01','2.02',...]
    is_material    BOOLEAN,              -- the derived label (NULL if no items listed)
    labeled_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eightk_item_material ON eightk_item (is_material);
CREATE INDEX IF NOT EXISTS idx_eightk_item_date     ON eightk_item (filing_date);

CREATE TABLE IF NOT EXISTS eightk_prediction (
    accession_no   TEXT NOT NULL REFERENCES raw_filing(accession_no),
    model_version  TEXT NOT NULL,        -- MLflow registered-model version
    proba          NUMERIC NOT NULL,     -- P(material)
    predicted      BOOLEAN NOT NULL,
    label          BOOLEAN,              -- ground truth if known (for eval)
    scored_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (accession_no, model_version)
);

CREATE TABLE IF NOT EXISTS eightk_eval_metric (
    model_version  TEXT NOT NULL,
    metric         TEXT NOT NULL,        -- 'roc_auc' | 'f1' | 'precision' | 'recall' | 'n_eval' | ...
    value          NUMERIC NOT NULL,
    eval_split     TEXT NOT NULL,        -- 'test' | 'live'
    evaluated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_version, metric, eval_split, evaluated_at)
);
