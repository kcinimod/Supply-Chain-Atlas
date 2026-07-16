-- v2 Phase 4: the market-reaction model's prediction/actuals loop.
--
-- reaction_prediction: one row per (event, horizon, model version), written at
--   PREDICTION time with actuals NULL. As daily bars arrive and a horizon's
--   trading-day window closes, the eval job backfills actual_car — this is the
--   genuine predict -> compare-with-actuals loop (v1 had none).
--
-- reaction_eval_metric: the per-horizon metric time series the 12:00 SGT eval
--   DAG appends to (hit_rate, brier, n) — model health over time.
--
-- ml_drift_baseline: the champion's score distribution FROZEN AT TRAIN TIME.
--   v1's drift check compared a live corpus against its own tail (self-
--   referential); v2 compares recent scores against this stored baseline.

CREATE TABLE IF NOT EXISTS reaction_prediction (
    event_id        BIGINT NOT NULL REFERENCES filing_event(event_id),
    horizon         TEXT   NOT NULL REFERENCES reaction_horizon(horizon),
    model_version   TEXT   NOT NULL,
    ticker          TEXT   NOT NULL,
    event_date      DATE   NOT NULL,
    proba_up        NUMERIC NOT NULL,        -- P(abnormal return > 0 over horizon)
    predicted_up    BOOLEAN NOT NULL,
    features        JSONB,                   -- prediction-time feature log (skew debugging)
    predicted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- actuals, backfilled once the horizon's trading-day window closes
    actual_car      NUMERIC,                 -- cumulative abnormal return over the window
    actual_up       BOOLEAN,
    window_closed_on DATE,
    evaluated_at    TIMESTAMPTZ,
    PRIMARY KEY (event_id, horizon, model_version)
);
CREATE INDEX IF NOT EXISTS idx_reaction_pred_open
    ON reaction_prediction (horizon) WHERE actual_car IS NULL;

CREATE TABLE IF NOT EXISTS reaction_eval_metric (
    model_version  TEXT NOT NULL,
    horizon        TEXT NOT NULL,
    metric         TEXT NOT NULL,            -- 'hit_rate' | 'brier' | 'n' | ...
    value          NUMERIC NOT NULL,
    evaluated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_version, horizon, metric, evaluated_at)
);

CREATE TABLE IF NOT EXISTS ml_drift_baseline (
    model_name     TEXT NOT NULL,
    model_version  TEXT NOT NULL,
    bin_fractions  NUMERIC[] NOT NULL,       -- 10-bin histogram of train scores in [0,1]
    n_scores       INT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_name, model_version)
);
