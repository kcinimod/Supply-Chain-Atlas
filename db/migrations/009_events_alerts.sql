-- v2 Phase 3: the event & signal layer — the product core.
--
-- filing_event: one row per detected CHANGE worth reacting to (a material 8-K,
--   a new/changed supply edge, a stake threshold crossing, an insider cluster).
--   Detection is idempotent: the natural key is UNIQUE NULLS NOT DISTINCT so
--   re-running a detector over an overlapping window is a no-op.
--
-- alert: the user-facing signal a rule engine derives from events (and from
--   system health). Replaces v1's four hardcoded dashboard rows with real,
--   deduplicated, queryable alerts. Streaming (fast-path) alerts land here too
--   via source='stream'.
--
-- reaction horizons: config-as-data so new horizons (6m, 1y) are INSERTs, not
--   schema changes. Trading-day counts, not calendar days.

CREATE TABLE IF NOT EXISTS filing_event (
    event_id     BIGSERIAL PRIMARY KEY,
    event_type   TEXT NOT NULL,          -- material_8k | supply_edge_new | supply_edge_changed
                                         -- | stake_change | insider_cluster
    cik          BIGINT NOT NULL,        -- the affected (tradeable) company
    ticker       TEXT,                   -- resolved trading symbol at detection time
    event_date   DATE NOT NULL,          -- the date the information became public
    accession_no TEXT REFERENCES raw_filing(accession_no),  -- provenance when single-filing
    details      JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE NULLS NOT DISTINCT (event_type, cik, event_date, accession_no)
);
CREATE INDEX IF NOT EXISTS idx_event_date ON filing_event (event_date);
CREATE INDEX IF NOT EXISTS idx_event_cik  ON filing_event (cik);

CREATE TABLE IF NOT EXISTS alert (
    alert_id   BIGSERIAL PRIMARY KEY,
    rule       TEXT NOT NULL,            -- which rule fired (machine name)
    severity   TEXT NOT NULL CHECK (severity IN ('info', 'watch', 'high')),
    cik        BIGINT,
    ticker     TEXT,
    title      TEXT NOT NULL,            -- short human-readable headline
    body       TEXT,                     -- supporting detail
    event_id   BIGINT REFERENCES filing_event(event_id),
    source     TEXT NOT NULL DEFAULT 'batch',  -- batch | stream | ml | system
    dedup_key  TEXT NOT NULL UNIQUE,     -- idempotent alerting across re-runs
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_alert_created ON alert (created_at DESC);

CREATE TABLE IF NOT EXISTS reaction_horizon (
    horizon      TEXT PRIMARY KEY,       -- '3d' | '1w' | '1m' | '3m' | ...
    trading_days INT NOT NULL CHECK (trading_days > 0),
    active       BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO reaction_horizon (horizon, trading_days) VALUES
    ('3d', 3), ('1w', 5), ('1m', 21), ('3m', 63)
ON CONFLICT (horizon) DO NOTHING;
