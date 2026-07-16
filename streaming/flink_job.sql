-- Flink SQL job #2 -- event-time windowed market context off the trade stream.
--
-- Reads atlas.trades as an unbounded table, assigns an event-time watermark on
-- each trade's exchange timestamp, and writes a 10-second tumbling VWAP / volume
-- / trade-count per symbol to atlas.trade_windows. Demonstrates the streaming
-- core: event time + watermarks + windowed aggregation, in Flink.
--
--   docker compose exec flink-jobmanager ./bin/sql-client.sh -f /opt/job/flink_job.sql

SET 'execution.runtime-mode' = 'streaming';
SET 'parallelism.default' = '1';

CREATE TABLE trades (
    symbol  STRING,
    price   DOUBLE,
    volume  INT,
    ts_ms   BIGINT,
    ts AS TO_TIMESTAMP_LTZ(ts_ms, 3),
    WATERMARK FOR ts AS ts - INTERVAL '5' SECOND
) WITH (
    'connector' = 'kafka',
    'topic' = 'atlas.trades',
    'properties.bootstrap.servers' = 'redpanda:9092',
    'properties.group.id' = 'flink-trades',
    'scan.startup.mode' = 'earliest-offset',
    'format' = 'json',
    'json.ignore-parse-errors' = 'true'
);

CREATE TABLE trade_windows (
    symbol        STRING,
    window_start  TIMESTAMP(3),
    window_end    TIMESTAMP(3),
    vwap          DOUBLE,
    total_volume  BIGINT,
    trade_count   BIGINT
) WITH (
    'connector' = 'kafka',
    'topic' = 'atlas.trade_windows',
    'properties.bootstrap.servers' = 'redpanda:9092',
    'format' = 'json'
);

INSERT INTO trade_windows
SELECT
    symbol,
    window_start,
    window_end,
    SUM(price * volume) / SUM(volume)  AS vwap,
    SUM(CAST(volume AS BIGINT))        AS total_volume,
    COUNT(*)                           AS trade_count
FROM TABLE(
    TUMBLE(TABLE trades, DESCRIPTOR(ts), INTERVAL '10' SECONDS)
)
GROUP BY symbol, window_start, window_end;
