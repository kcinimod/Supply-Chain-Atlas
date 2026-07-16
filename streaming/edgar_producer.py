"""Producer #1 — the live EDGAR filing firehose.

Polls SEC EDGAR's `getcurrent` Atom feed (the latest filings across *all* filers,
refreshed continuously during filing hours) and publishes each new filing to
Kafka. This is a genuine high-volume event stream (thousands of filings/day), so
Kafka earns its place: the producer decouples from every consumer, messages are
replayable from offsets, and many consumers (alerting, a graph loader, a
dashboard) fan out from one topic.

Deliberately mirrors the batch poller's discipline: dedup by accession number
(the natural key), reuse the rate-limited SEC http client, publish nothing
durable to the lake (the archive stays Airflow/Dagster's job -- see the RiskPulse
"the moat is the archive, not the fetch" lesson).

    uv run python -m streaming.edgar_producer            # loop
    uv run python -m streaming.edgar_producer --once      # single poll (tests)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
import xml.etree.ElementTree as ET

from kafka import KafkaProducer

from ingestion import http_client
from streaming import config

log = logging.getLogger("edgar_producer")

_ATOM = "{http://www.w3.org/2005/Atom}"
GETCURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
              "&type=&company=&dateb=&owner=include&count=100&output=atom")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CIK_IN_HREF = re.compile(r"/data/(\d+)/")


def parse_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    entries: list[dict] = []
    for e in root.findall(f"{_ATOM}entry"):
        idt = e.findtext(f"{_ATOM}id") or ""
        if "accession-number=" not in idt:
            continue
        accession = idt.split("accession-number=")[-1].strip()
        cat = e.find(f"{_ATOM}category")
        link = e.find(f"{_ATOM}link")
        href = link.get("href") if link is not None else ""
        cik_m = _CIK_IN_HREF.search(href)
        summary = e.findtext(f"{_ATOM}summary") or ""
        filed_m = _DATE.search(summary)
        entries.append({
            "accession": accession,
            "cik": int(cik_m.group(1)) if cik_m else None,
            "form": cat.get("term") if cat is not None else None,
            "title": (e.findtext(f"{_ATOM}title") or "").strip(),
            "filed": filed_m.group(0) if filed_m else None,
            "updated": e.findtext(f"{_ATOM}updated"),
            "link": href,
        })
    return entries


def _producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=config.KAFKA_BOOTSTRAP,
        key_serializer=lambda k: (k or "").encode(),
        value_serializer=lambda v: json.dumps(v).encode(),
        acks="all",
    )


def _mark_new(accessions: list[str]) -> set[str]:
    """Durable dedup (v2): claim keys in stream_seen; only rows we actually
    inserted are 'new'. Survives producer restarts — no feed replay."""
    from db.pool import connection
    new: set[str] = set()
    with connection() as conn:
        with conn.cursor() as cur:
            for acc in accessions:
                cur.execute(
                    "INSERT INTO stream_seen (source, key) VALUES ('edgar_live', %s) "
                    "ON CONFLICT (source, key) DO NOTHING", (acc,))
                if cur.rowcount:
                    new.add(acc)
            # keep the dedup table bounded — the feed only shows recent filings
            cur.execute("DELETE FROM stream_seen "
                        "WHERE source = 'edgar_live' AND seen_at < now() - interval '7 days'")
    return new


def run(once: bool = False) -> None:
    producer = _producer()
    session_seen: set[str] = set()   # cheap first-level filter within a session
    while True:
        xml = http_client.get_text(GETCURRENT, allow_missing={403, 404})
        entries = parse_feed(xml) if xml else []
        candidates = [f for f in entries if f["accession"] not in session_seen]
        new_keys = _mark_new([f["accession"] for f in candidates])
        published = 0
        for f in candidates:
            session_seen.add(f["accession"])
            if f["accession"] not in new_keys:
                continue
            producer.send(config.TOPIC_FILINGS, key=f["accession"], value=f)
            published += 1
        producer.flush()
        log.info("published %d new filings (%d in feed)", published, len(entries))
        if once:
            break
        time.sleep(config.POLL_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    run(once=ap.parse_args().once)
