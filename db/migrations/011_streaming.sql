-- v2 Phase 6: streaming gets durable state and a real landing zone.
--
-- stream_seen: producer dedup that survives restarts. v1 deduped in a Python
--   set that reset with the process, replaying the whole feed after every
--   restart — this table makes the producers stateless across restarts.
--
-- trade_window: the Flink job's output finally LANDS somewhere. A sink
--   consumer upserts atlas.trade_windows here; the dashboard reads the latest
--   windows per symbol as live market context. (v1 wrote windows to a Kafka
--   topic nothing consumed.)

CREATE TABLE IF NOT EXISTS stream_seen (
    source  TEXT NOT NULL,          -- 'edgar_live' | ...
    key     TEXT NOT NULL,          -- the stream's natural key (accession, ...)
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, key)
);
CREATE INDEX IF NOT EXISTS idx_stream_seen_age ON stream_seen (seen_at);

CREATE TABLE IF NOT EXISTS trade_window (
    symbol        TEXT NOT NULL,
    window_start  TIMESTAMPTZ NOT NULL,
    window_end    TIMESTAMPTZ NOT NULL,
    vwap          NUMERIC(18, 6),
    total_volume  BIGINT,
    trade_count   BIGINT,
    inserted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, window_start)
);
CREATE INDEX IF NOT EXISTS idx_trade_window_recent ON trade_window (window_start DESC);
