-- v2 Phase 1: universe-as-data + market price history.
--
-- universe_member replaces the hardcoded Python dict in ingestion/config.py:
-- scope changes become data changes (edit db/seeds/universe.csv, re-run
-- `python -m ingestion load-companies`), and a future SaaS tenant gets a
-- universe table row-set instead of a code fork. dim_company.in_universe stays
-- as the derived flag the rest of the pipeline already joins on.

CREATE TABLE IF NOT EXISTS universe_member (
    id          BIGSERIAL PRIMARY KEY,
    ticker      TEXT,                        -- NULL for private owners (no listing)
    cik         BIGINT,                      -- declared (private owners) or resolved from reference
    title       TEXT,
    module      TEXT NOT NULL,               -- sector module label, e.g. A_semiconductors
    side        TEXT NOT NULL CHECK (side IN ('supplier', 'customer', 'owner')),
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ticker IS NOT NULL OR cik IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_universe_ticker ON universe_member (ticker) WHERE ticker IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_universe_cik    ON universe_member (cik)    WHERE cik IS NOT NULL AND ticker IS NULL;

-- Daily OHLCV bars for universe tickers + the benchmark (SPY). Source of truth
-- for reaction labels and actuals-backfill; bronze snapshots of each ingest run
-- also land in the Parquet lake (data/lake/prices/dt=.../).
CREATE TABLE IF NOT EXISTS price_daily (
    ticker      TEXT NOT NULL,
    trade_date  DATE NOT NULL,
    open        NUMERIC(18, 6),
    high        NUMERIC(18, 6),
    low         NUMERIC(18, 6),
    close       NUMERIC(18, 6),
    adj_close   NUMERIC(18, 6),
    volume      BIGINT,
    source      TEXT NOT NULL DEFAULT 'yfinance',
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);

CREATE INDEX IF NOT EXISTS idx_price_daily_date ON price_daily (trade_date);
