"""Consumer #2 — lands the Flink trade windows in Postgres.

v1's Flink job wrote 10-second VWAP windows to atlas.trade_windows and nothing
read them. This sink upserts each window into `trade_window`, which the
serving API exposes as live market context for the focused company. The full
fast path is now: trades -> Kafka -> Flink (event-time windows + watermarks)
-> Kafka -> this sink -> Postgres -> dashboard.

    uv run python -m streaming.window_sink                 # run
    uv run python -m streaming.window_sink --timeout 8      # consume 8s (tests)
"""
from __future__ import annotations

import argparse
import json
import logging

from kafka import KafkaConsumer

from db.pool import connection
from streaming import config

log = logging.getLogger("window_sink")


def _upsert(rows: list[dict]) -> int:
    if not rows:
        return 0
    with connection() as conn:
        with conn.cursor() as cur:
            for w in rows:
                cur.execute(
                    "INSERT INTO trade_window "
                    "  (symbol, window_start, window_end, vwap, total_volume, trade_count) "
                    "VALUES (%s, %s::timestamptz, %s::timestamptz, %s, %s, %s) "
                    "ON CONFLICT (symbol, window_start) DO UPDATE SET "
                    "  vwap = EXCLUDED.vwap, total_volume = EXCLUDED.total_volume, "
                    "  trade_count = EXCLUDED.trade_count, inserted_at = now()",
                    (w.get("symbol"), w.get("window_start"), w.get("window_end"),
                     w.get("vwap"), w.get("total_volume"), w.get("trade_count")))
    return len(rows)


def run(timeout: int | None = None) -> None:
    consumer = KafkaConsumer(
        config.TOPIC_TRADE_WINDOWS,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        group_id="atlas-window-sink",
        value_deserializer=lambda v: json.loads(v.decode()),
        # kafka-python-ng wants +inf (not None) for "block forever"
        consumer_timeout_ms=(timeout * 1000) if timeout else float("inf"),
    )
    batch: list[dict] = []
    total = 0
    for msg in consumer:
        batch.append(msg.value)
        if len(batch) >= 50:
            total += _upsert(batch)
            batch.clear()
            log.info("landed %d windows so far", total)
    total += _upsert(batch)
    log.info("window sink done: %d windows landed", total)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=None,
                    help="stop after N seconds of no messages (for tests)")
    run(timeout=ap.parse_args().timeout)
