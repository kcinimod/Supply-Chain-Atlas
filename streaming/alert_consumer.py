"""Consumer #1 — watchlist alerting off the filing firehose.

Reads the `atlas.filings` topic and reacts to filings that touch a universe
company: a new beneficial-ownership stake (13D/G), a material event (8-K), or any
in-scope form. Emits an alert to `atlas.alerts` (and stdout). This is the honest
Kafka use flagged back in Phase 3: one live event, many independent reactors.

Note (a real limitation worth stating): `getcurrent` shows one CIK per filing --
the *filer*. So company/owner-filed forms (8-K, 10-K, 13F, and 13D/G filed by our
institutional owners) match the universe directly; an insider Form 4 shows the
*insider's* CIK, so catching "Form 4 about a universe issuer" needs a follow-up
lookup (left as enrichment, since the high-signal alerts are company/owner-filed).

    uv run python -m streaming.alert_consumer                 # run
    uv run python -m streaming.alert_consumer --timeout 8      # consume 8s (tests)
"""
from __future__ import annotations

import argparse
import json
import logging

from kafka import KafkaConsumer, KafkaProducer

from ingestion import store
from streaming import config

log = logging.getLogger("alert_consumer")

# Forms that make a universe filing alert-worthy, with a priority label.
HIGH_PRIORITY = {
    "SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A",  # activist stakes
    "8-K", "8-K/A",                                          # material events
}
OWNERSHIP = {
    "SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A", "13F-HR", "13F-HR/A",
}


def _classify(form: str | None) -> str | None:
    if form in HIGH_PRIORITY:
        return "high"
    if form in OWNERSHIP:
        return "ownership"
    return "info"


def run(timeout: int | None = None) -> None:
    universe = store.universe_ciks()
    log.info("watchlist = %d universe CIKs; bootstrap=%s", len(universe), config.KAFKA_BOOTSTRAP)

    consumer = KafkaConsumer(
        config.TOPIC_FILINGS,
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
        group_id="atlas-alerting",
        value_deserializer=lambda v: json.loads(v.decode()),
        # kafka-python-ng wants +inf (not None) for "block forever"
        consumer_timeout_ms=(timeout * 1000) if timeout else float("inf"),
    )
    producer = KafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode())

    seen = alerts = 0
    for msg in consumer:
        f = msg.value
        seen += 1
        if f.get("cik") in universe:
            priority = _classify(f.get("form"))
            alert = {
                "priority": priority,
                "cik": f["cik"], "form": f.get("form"),
                "title": f.get("title"), "accession": f.get("accession"),
                "filed": f.get("filed"), "link": f.get("link"),
            }
            producer.send(config.TOPIC_ALERTS, value=alert)
            _persist(alert, priority)
            alerts += 1
            log.warning("ALERT [%s] %s  (%s)", alert["priority"], alert["title"],
                        alert["accession"])
    producer.flush()
    log.info("processed %d filings, emitted %d watchlist alerts", seen, alerts)


def _persist(alert: dict, priority: str) -> None:
    """v2: the fast path lands in the same `alert` table the dashboard serves
    (source='stream'), so a live filing shows up in the UI within a poll tick.
    Dedup key = accession, so batch re-detection later won't double-notify."""
    from events import store as event_store
    severity = {"high": "high", "ownership": "watch"}.get(priority, "info")
    try:
        event_store.insert_alerts([{
            "rule": f"stream_filing_{priority}", "severity": severity,
            "cik": alert["cik"], "ticker": None,
            "title": f"Live filing — {alert.get('form')}: {alert.get('title')}",
            "body": f"Filed {alert.get('filed')}; {alert.get('link')}",
            "source": "stream",
            "dedup_key": f"stream:{alert.get('accession')}",
        }])
    except Exception as exc:  # a DB hiccup must not stall the stream
        log.error("failed to persist stream alert %s: %s", alert.get("accession"), exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=None,
                    help="stop after N seconds of no messages (for tests)")
    run(timeout=ap.parse_args().timeout)
