"""Streaming settings. KAFKA_BOOTSTRAP defaults to the host-facing Redpanda
listener; inside a container set it to redpanda:9092 (the internal listener)."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092")

# Topics
TOPIC_FILINGS = "atlas.filings"              # live EDGAR filing firehose
TOPIC_TRADES = "atlas.trades"                # real-time equity trades
TOPIC_TRADE_WINDOWS = "atlas.trade_windows"  # Flink 10s VWAP windows (job output)
TOPIC_ALERTS = "atlas.alerts"                # emitted watchlist alerts
# (v1's atlas.news / GDELT producer was dropped in v2: it had no consumer and
# no story; multi-source news belongs in the batch Source framework when added.)

POLL_INTERVAL = int(os.environ.get("STREAM_POLL_INTERVAL", "60"))
