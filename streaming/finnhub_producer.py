"""Producer #2 — real-time equity trades for the universe tickers.

Two modes:
* **live**  — Finnhub WebSocket (`FINNHUB_API_KEY` in .env; free tier = 50 symbols,
  which comfortably covers our hub tickers). Genuine high-velocity tick stream.
* **synthetic** — a deterministic random-walk generator (no key, no network) so the
  Flink windowing/join job can be developed and verified offline.

Publishes one message per trade to Kafka `atlas.trades`:
    {symbol, price, volume, ts_ms}

    uv run python -m streaming.finnhub_producer --mode synthetic --seconds 20
    uv run python -m streaming.finnhub_producer --mode live
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time

from kafka import KafkaProducer

from streaming import config

log = logging.getLogger("finnhub_producer")

# Hub tickers most likely to react to a filing event (kept <= 50 for the free tier).
SYMBOLS = [
    "NVDA", "AMD", "AMAT", "LRCX", "MRVL", "TSLA", "GM", "F", "RIVN",
    "LMT", "RTX", "NOC", "GD", "BA", "LLY", "PFE", "MRK", "AMGN",
    "EQIX", "VRT", "CEG", "ALB", "RKLB", "CRL", "DHR",
]


def _producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        key_serializer=lambda k: (k or "").encode(),
        value_serializer=lambda v: json.dumps(v).encode())


def run_synthetic(seconds: int, base_ts_ms: int) -> None:
    """Deterministic random walk (no Math.random dependency on wall clock).

    base_ts_ms=0 means "anchor event time to the wall clock now" — the mode
    the always-on compose service uses, so restarts keep producing FRESH
    window keys instead of overwriting the same fixed 20 seconds forever."""
    if base_ts_ms == 0:
        base_ts_ms = int(time.time() * 1000)
    producer = _producer()
    price = {s: 100.0 + i for i, s in enumerate(SYMBOLS)}
    sent = 0
    for tick in range(seconds * 5):                 # ~5 trades/sec
        s = SYMBOLS[tick % len(SYMBOLS)]
        # pseudo-random walk from a hash of (symbol, tick) -> no RNG needed
        step = ((hash((s, tick)) % 200) - 100) / 100.0
        price[s] = max(1.0, price[s] + step)
        ts_ms = base_ts_ms + tick * 200
        producer.send(config.TOPIC_TRADES, key=s,
                      value={"symbol": s, "price": round(price[s], 2),
                             "volume": 100 + (tick % 50), "ts_ms": ts_ms})
        sent += 1
        time.sleep(0.05)
    producer.flush()
    log.info("synthetic: published %d trades across %d symbols", sent, len(SYMBOLS))


def run_live() -> None:
    import websocket  # websocket-client, only needed for live mode

    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY not set (free key at finnhub.io) — "
                         "or use --mode synthetic")
    producer = _producer()

    def on_open(ws):
        for s in SYMBOLS:
            ws.send(json.dumps({"type": "subscribe", "symbol": s}))
        log.info("subscribed to %d symbols", len(SYMBOLS))

    def on_message(ws, message):
        msg = json.loads(message)
        for t in msg.get("data", []):
            producer.send(config.TOPIC_TRADES, key=t.get("s"),
                          value={"symbol": t.get("s"), "price": t.get("p"),
                                 "volume": t.get("v"), "ts_ms": t.get("t")})

    ws = websocket.WebSocketApp(
        f"wss://ws.finnhub.io?token={api_key}",
        on_open=on_open, on_message=on_message)
    ws.run_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["synthetic", "live"], default="synthetic")
    ap.add_argument("--seconds", type=int, default=20, help="synthetic run length")
    ap.add_argument("--base-ts-ms", type=int, default=1_770_000_000_000,
                    help="synthetic start timestamp (ms) for reproducibility")
    a = ap.parse_args()
    if a.mode == "synthetic":
        run_synthetic(a.seconds, a.base_ts_ms)
    else:
        run_live()
