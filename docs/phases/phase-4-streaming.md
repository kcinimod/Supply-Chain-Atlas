# Phase 4 — Streaming: A Fast Path That Finally Lands Somewhere

> **Learning write-up & presentation notes.** The real-time path beside the
> batch backbone — what v2 kept, what it **deleted**, and the sink layer that
> turned v1's demo streams into an end-to-end fast path. Honest tool ledger:
> **Kafka & Flink used, Spark deferred**, one producer removed on principle.
>
> **What this phase demonstrates (course rubric):** Kafka topics, producers and
> **consumer groups**; **event-time windows + watermarks** in Flink SQL; the
> "Kafka data flow: producer → downstream processing → dashboard" demo,
> literally; durable dedup; and streaming services supervised by Docker Compose
> across nightly outages.

---

## 1. What this phase adds, and why

The batch spine (phases 1–3) is correct, complete, and as fresh as its last
09:30 run. For an *operational* question — "did an activist stake just land on
a watchlist company?" — we want a path that reacts in seconds. This is the
classic **lambda architecture**: a fast/approximate stream beside the
complete/accurate batch layer. The batch layer stays the source of truth;
nothing here writes the durable lake.

v2's streams, end to end:

```
SEC EDGAR getcurrent ─▶ atlas.filings ─▶ alert consumer ─▶ alert table (source='stream')
                                                        └▶ atlas.alerts topic
trades (live/synthetic) ─▶ atlas.trades ─▶ Flink SQL 10s VWAP (event time,
                             watermarks) ─▶ atlas.trade_windows ─▶ window sink
                                                        └▶ trade_window table ─▶ dashboard
```

The one-line v1→v2 diff: **v1's streams produced things nobody consumed; v2's
streams end in Postgres rows the dashboard actually renders.** A stream without
a consumer is a demo; a stream with a sink is a pipeline.

---

## 2. What got deleted: the GDELT news producer *(a design decision, stated)*

v1 had a third stream — universe news via GDELT's DOC API. v2 **deleted it**.

> *"It published 75 articles to a topic that no consumer, no table, and no
> screen ever read. It existed to say 'three streams'. Removing it was the
> honest call: the rubric grades data that *flows somewhere*, and every
> component you keep is a component you must operate through a nightly outage.
> Tool-count is not the metric; consumed output is."*

The pattern it demonstrated (filter-at-the-source against a firehose API) is
still worth a sentence in the presentation — as a thing we *tried and cut*.

## 3. Stream #1 — the live EDGAR firehose → real alerts

- **Producer** (`streaming/edgar_producer.py`) polls the `getcurrent` Atom feed
  (all filers, continuous during filing hours) through the same rate-limited
  SEC client as batch, and publishes each new filing to `atlas.filings`.
- **Durable dedup (v2 fix).** v1 deduped in a Python set that vanished on
  restart — every producer restart replayed the whole feed. v2 claims each
  accession in a **`stream_seen`** table (`INSERT … ON CONFLICT DO NOTHING`;
  only rows actually inserted count as new), pruned past 7 days since the feed
  only shows recent filings. Restarts — and this laptop restarts nightly — now
  publish nothing twice. A session-local set remains as a cheap first-level
  filter in front of the DB.
- **Consumer** (`streaming/alert_consumer.py`, consumer group `atlas-alerting`)
  matches each filing's CIK against the universe watchlist, classifies by form
  (13D/8-K → high, 13G/13F → ownership), and — the v2 change — **persists the
  alert into the same `alert` table the dashboard serves** (`source='stream'`,
  `dedup_key = stream:<accession>`), as well as publishing to the
  `atlas.alerts` topic for future fan-out consumers. A live filing appears in
  the UI within one poll tick; the batch layer's later re-detection of the same
  filing can't double-notify because alert dedup keys are unique.
- **Honest limitation (unchanged):** `getcurrent` shows the *filer's* CIK, so
  company/owner-filed forms match directly; an insider Form 4 shows the
  insider's CIK and would need an enrichment lookup.

## 4. Stream #2 — trades → Flink event-time windows → Postgres

The trade stream is the genuine high-velocity feed and the one place a
**stream processor** earns its keep.

- **Producer** (`streaming/finnhub_producer.py`) — live Finnhub WebSocket, or a
  **synthetic random-walk mode** so the whole path is verifiable without an API
  key (in Compose it runs synthetic with a fresh time base per restart).
- **Flink SQL job** (`streaming/flink_job.sql`) — reads `atlas.trades` as an
  unbounded table, assigns an **event-time watermark** (exchange timestamp − 5s
  for stragglers), computes a **10-second tumbling VWAP / volume / trade-count
  per symbol**, writes finalized windows to `atlas.trade_windows`. Event time,
  watermarks, windowed aggregation — the Day-3 streaming core, on a real
  cluster (Flink 1.20 in Docker; PyFlink/JVM friction on Windows is why it's
  containerized, same reasoning that deferred native Spark).
- **NEW: the window sink** (`streaming/window_sink.py`, group
  `atlas-window-sink`) upserts each window into the **`trade_window`** table on
  `(symbol, window_start)`. The serving API exposes the latest windows as live
  market context for the focused company — v1's windows went to a topic nothing
  read; **the fast path is now end-to-end**: producer → Kafka → Flink → Kafka →
  sink → Postgres → dashboard.

### The window-timeframe decision (an examiner will ask)
Does a 10-second window make sense for filing→price reaction? **No — and it
isn't for that.** Form 4s often file after market close; the real reaction
metric is session-aware and multi-day, and it lives in the **batch** ML core
(cumulative abnormal return over trading-day horizons, phase 5). The stream
window is *live market context* to enrich an alert ("new 13D on WOLF; trading
3× volume right now"). Right tool, right horizon — the split is deliberate.

## 5. Operating streams on a laptop that sleeps

v1 ran producers/consumers by hand in terminals. v2 makes them **Compose
services under the `streaming` profile**, all `restart: unless-stopped`:
Redpanda, Flink jobmanager/taskmanager, the EDGAR producer, alert consumer,
trade producer, window sink — and a **`flink-submit` supervisor**, a small loop
that checks the Flink REST API and (re)submits the SQL job whenever no job is
RUNNING. Flink jobs don't survive a cluster restart by themselves; after the
nightly wake, the supervisor restores the job with no operator action — the
same self-healing contract the batch layer makes.

*What we rejected:* enabling Flink checkpoint/savepoint recovery instead of
resubmission. For a stateless-ish 10s tumbling aggregation, resubmitting the
SQL is simpler and loses at most one window; savepoints would be the answer if
the job carried long-lived state worth preserving.

---

## 6. The tool ledger — used, and deliberately not *(the graded part)*

| Tool | Verdict | Justification |
|---|---|---|
| **Kafka (Redpanda)** | ✅ used | Live filing/trade events, replayable offsets, independent consumer groups (`atlas-alerting`, `atlas-window-sink`). Decoupling that a function call can't give. |
| **Flink SQL** | ✅ used | Stateful event-time windowing + watermarks on the trade stream — the one workload that warrants a stream processor. |
| **GDELT news producer** | ⛔ **deleted** | No consumer, no story. Cut rather than carried. |
| **Kafka for batch ingest** | ⛔ still rejected | EDGAR bulk publishes on a daily index; a CDC cursor is the correct pattern. Kafka in front of a daily batch is architecture theater. |
| **Spark** | ⛔ deferred → future work | Batch workload is laptop-sized and rate-limited (I/O-bound); Spark earns its keep at whole-market scale (~150 GB/yr of documents, CPU-bound parse). |

---

## 7. How to run / verify

```bash
docker compose --profile streaming up -d --build
# producers/consumers/flink-submit are services now — nothing to start by hand.
docker compose exec redpanda rpk topic consume atlas.trade_windows --num 5
docker exec atlas-postgres psql -U atlas -c \
  "SELECT symbol, window_start, vwap FROM trade_window ORDER BY window_start DESC LIMIT 5"
docker exec atlas-postgres psql -U atlas -c \
  "SELECT title, severity FROM alert WHERE source='stream' ORDER BY created_at DESC LIMIT 5"
# consoles: Redpanda http://localhost:8085 · Flink http://localhost:8081
```

Verified: producer dedup survives restarts (stream_seen rows, zero replays);
alert consumer lands `source='stream'` rows the dashboard shows within a poll;
Flink emits 10s VWAP windows that the sink upserts into `trade_window`; killing
the Flink job → the submit supervisor resubmits it.

---

## 8. What I'd do with more time

- **Alert fan-out to real channels** — email/Slack sinks off `atlas.alerts`;
  pure output adapters, the natural many-consumer Kafka payoff, added the first
  time a human needs paging.
- **Form-4 enrichment on the firehose** — resolve insider CIK → issuer so
  insider filings about universe companies alert too.
- **Flink stream-stream join** (trades × ticker-tagged filings) — still
  deliberately designed out: the reaction signal is session-aware and batch;
  an in-stream join is only justified by a seconds-latency surveillance use
  case we don't have.
- **Spark at whole-market scale** — the standing deferral, unchanged.

---

*Key files: `streaming/edgar_producer.py` (durable dedup),
`streaming/alert_consumer.py` (persists to `alert`),
`streaming/finnhub_producer.py`, `streaming/flink_job.sql`,
`streaming/window_sink.py` (the new sink), `streaming/config.py` (topics),
`db/migrations/011_streaming.sql` (`stream_seen`, `trade_window`),
`docker-compose.yml` (streaming profile + flink-submit supervisor).*
