# Supply Chain Atlas v2 — Full Revamp Plan

*Drafted 2026-07-15 from a four-track code review of v1. Decisions already locked
by the owner: full revamp; ML core = 8-K classifier as filter feeding a new
market-reaction model; orchestrator choice delegated (decision below).
Assessment deliverables (video + resume-bullet PDF) due **14 Sep 2026**.*

### Owner constraints (added 2026-07-15, approved with go-ahead)

1. **Laptop-off window 00:00–09:00 SGT.** Everything must tolerate a nightly
   9-hour outage: Docker services use `restart: unless-stopped` so the stack
   (Airflow scheduler included) comes back when Docker Desktop starts; the
   Airflow scheduler then fires the latest missed run of each DAG
   (`catchup=False` + cursor-based ingest = gaps self-heal); the EDGAR poller's
   day cursor re-walks every missed index day. No schedule may sit inside the
   off window.
2. **Ingest auto-starts with Docker** — the daily pipeline runs at **09:30
   SGT** (laptop reliably on); if the laptop wakes later, Airflow triggers the
   missed run immediately on scheduler start.
3. **Market-reaction model is multi-horizon:** predict at **3 trading days,
   1 week, 1 month, 3 months** — horizons defined in config/DB so new ones
   (6m, 1y…) are additive, not structural. Predictions keyed by
   (event, horizon, model_version); each horizon's actuals backfill when its
   window closes.
4. **Evaluation DAG runs at 12:00 SGT** (not 08:00).
5. **Web design follows `DESIGN_PATTERNS.md`** — keep v1's visual system, and
   add first-class **change flagging**: new/changed/removed relationships,
   stake deltas, and model outlook (per-horizon direction) surfaced as badges
   on nodes/edges plus a "Changes" feed view.
6. **`docs/phases/*.md` get revised** to describe v2.

---

## 1. Product reframing

v1's story: "turn EDGAR filings into a relationship graph."
v2's story: **"detect supply-chain and ownership *changes* in regulatory
filings and turn them into investable signals."**

The graph is the substrate; the product is the *event*: a supplier gains or
loses a major customer, a stake crosses a threshold, insiders cluster-buy,
a material 8-K lands. Every event gets: (a) a real alert, (b) a predicted
short-horizon market reaction, (c) a later comparison against what actually
happened. That closes the rubric's predict → compare → evaluate → alert loop
and *is* the SaaS value proposition.

---

## 2. Orchestrator decision: **Apache Airflow 3, in Docker Compose**

Reasoning (this was my call to make):

- Two of the rubric's four named demo examples are literally "Airflow DAG:
  show when the workflow runs, task dependencies, and how failed tasks are
  retried" and "Docker Compose: show which services are started together."
  Airflow-in-Compose hits both with zero narrative risk in front of a grader
  who taught Airflow.
- The port is cheap because v1 did the hard part already: all business logic
  lives in orchestrator-agnostic idempotent module functions; the Dagster
  assets are 3–5-line wrappers. Review estimate: 3–5 focused days.
- Airflow's per-task `retries`/`retry_delay` is a net *gain* — v1 declares no
  RetryPolicy on any asset today.
- What we lose (Dagster's unified dbt lineage, first-class asset checks) we
  recover with **astronomer-cosmos** (dbt models as a task group) and check
  tasks with explicit pass/warn/fail semantics.
- v1's "Dagster over Airflow" defense stays in the presentation as a design
  discussion — we can honestly say we ran both and chose Airflow for
  operational uniformity (and it demos better).

---

## 3. Target architecture

```
                          SOURCES (pluggable Source interface)
        ┌───────────────────┬─────────────────────┬──────────────────────┐
        │ EDGAR (filings)   │ Market data (OHLCV) │ [future: news, intl] │
        │ backfill + CDC    │ yfinance daily      │                      │
        └────────┬──────────┴──────────┬──────────┴──────────────────────┘
                 ▼                     ▼
   BRONZE   gzipped docs on disk   Parquet lake (prices, partitioned)      idempotent, resumable
                 ▼                     ▼
   SILVER   per-filing parsers → Postgres        price_daily → Postgres
                 ▼
   GOLD     dbt star schema (dims + 5 edge facts + SCD2)  + incremental facts
                 ▼
   EVENTS   change-detection layer → filing_event + alert tables (REAL alert engine)
                 ▼
   ML       stage 1: 8-K substance classifier (filter)
            stage 2: market-reaction model — predict T+1..T+3 abnormal
            return/volume for material events; predictions stored at event time;
            actuals backfilled as prices arrive; daily 08:00 evaluation;
            drift vs FROZEN training baseline; MLflow registry @champion alias
                 ▼
   SERVING  FastAPI (graph / events / alerts / predictions / health)
            live dashboard (reuses v1 SVG graph components, real data, real alerts)
                 ▼
   FAST PATH  EDGAR live feed → Redpanda → alert consumer → Postgres + atlas.alerts
              trades → Flink SQL 10s VWAP windows → sink consumer → Postgres → dashboard

   Orchestrated by Airflow 3 (Docker Compose), 4 DAGs. MLflow server in Compose.
```

Storage stays **Postgres + pgvector** (one store for relational, SCD2, vector;
justified in v1 and still correct at this scale). **Spark stays deferred with
justification** — workload is laptop-sized and I/O-bound; lecturer explicitly
says not every tool is needed, and the deferral argument is itself
presentation material.

---

## 4. What the review found → keep / rebuild verdicts

### Keep (highest-value v1 code, ported largely verbatim)
- **Per-filing-type parsers** (`transform/form4.py`, `form13f.py`,
  `exhibit21.py`, `sched13dg.py`) — pure functions encoding hard-won SEC
  domain knowledge (dual form spellings post-2024 rename, 13F dollar-unit
  cutover, SGML slicing). Hardest to recreate.
- **Idempotency pattern** — upsert + parse-log + `NOT EXISTS` selection;
  becomes the template for every new pipeline.
- **HTTP client** (rate limiter + tenacity retry + `allow_missing` 403
  handling) and **bronze archive** (gzip, date-partitioned, sha256).
- **Freshness check** (`ingestion/freshness.py`) — learned per-source
  thresholds, rename-proof logical grouping. Production-grade as-is.
- **dbt star schema** — conformed dims, role-playing dim_company, factless
  bridge, dual SCD2 (snapshot + window function), FK relationship tests.
- **NLP overlay core** — retrieve-then-extract, schema-constrained JSON on
  local Ollama (qwen2.5:3b), tiered honesty (resolved/named/unnamed),
  provenance quotes, gold-set harness design.
- **8-K classifier** (TF-IDF + LogReg, time-ordered split, leakage scrubbing)
  — becomes stage 1 of the ML core.
- **EDGAR live-feed producer + alert consumer** — the one genuine stream.
- **DESIGN_PATTERNS.md + dashboard SVG graph components** — reused as the
  front-end spec/components for the live dashboard.

### Rebuild / build new
| Area | Problem found | v2 action |
|---|---|---|
| Source coupling | CIK + accession_no are PKs everywhere; no source abstraction | `Source` interface; `document` table with synthetic key + `source` discriminator; entity table decoupled from CIK |
| Universe | Hardcoded Python dict | DB-seeded universe table (CSV seed), scope change = data change |
| DB access | `db/pool.py` opens a connection per statement (no pool) | `psycopg_pool`, batched log writes |
| Market data | **None exists** (only Form-4 reported prices) | yfinance daily OHLCV → Parquet lake → `price_daily`; CIK→ticker mapping table |
| Eval loop | `evaluate()` re-scores training-time labels ("live" is mislabeled) | True prediction-vs-actuals: predict at event time, backfill realized abnormal returns, rolling hit-rate/MAE, daily 08:00 |
| Drift | Baseline = in-sample tail of same corpus (self-referential); WARN-only | Frozen training-time baseline persisted at train; PSI vs bounded recent slice; ERROR severity gates + alert |
| Registry | Version = `max(int)`; no aliases | MLflow server in Compose; `@champion` alias; serving loads `models:/…@champion` (matches Day 4 exactly); shadow-score challenger versions (stretch) |
| Alerts | Dashboard rail = 4 hardcoded HTML rows; "live" dot cosmetic | Real rule engine → `alert` table (+ Kafka topic); dashboard reads it live |
| Serving | No HTTP API anywhere | FastAPI service (graph/events/alerts/predictions/health), Dockerized |
| Streaming sinks | Flink windows + news topic consumed by nothing; in-memory dedup resets on restart | Sink consumer persists trade windows; alert consumer writes Postgres; durable dedup; drop the news producer (no consumer, no story) |
| Orchestration | `selection="*"` retrains the model daily; no retries | 4 Airflow DAGs (below); per-task retries; check tasks that can fail |
| dbt | Full rebuilds of growing facts; no staging tests | Incremental materializations on big facts; staging + snapshot tests |
| Validation | Upstream JSON trusted structurally; no value-range checks | Pydantic schemas at source boundaries; domain checks (pct ∈ [0,100], codes ∈ known sets) |
| Tests | **Zero project tests** | pytest + fixture suite for parsers (pure functions — cheap), stores, event logic |
| NLP pct fabrication | All model variants invent percentages ("more than 10%" → fake number) | Post-filter: accept `pct` only if the number literally appears in the source quote; curate the 33-filing gold set; harness as regression gate |
| Config/packaging | `main.py` stub; creds hardcoded in compose; PII default in config | Full Compose stack (postgres, airflow, mlflow, api, redpanda/flink profile); env-driven config, no baked secrets |

---

## 5. ML core design (the rubric's 30-mark centerpiece)

**Stage 1 — filter (kept):** 8-K substance classifier gates which filings are
"material events." Extended event set: material 8-K, new/changed supply edge,
13D/G stake threshold crossing, insider cluster buys.

**Stage 2 — market-reaction model (new):** for each material event, predict
direction (and magnitude bucket) of the 3-day abnormal return vs SPY, plus
volume spike. Features: event type, materiality score, filing-text features,
sector module, pre-event volatility/drift. Trained on historical events ×
historical prices (the backfilled archive gives years of labeled events for
free).

**The loop (this is what gets graded):**
1. Event lands → prediction written to `reaction_prediction` with model
   version + features (prediction log, Day 4 style).
2. Daily price ingest closes event windows → actuals backfilled.
3. **Daily 08:00 evaluation DAG** — rubric's literal example — computes
   realized hit-rate/error, logs to MLflow, writes the metric time series.
4. Alert fires if performance degrades or drift PSI breaches → visible in
   dashboard + Airflow.
5. Retrain DAG (weekly/triggered) registers a new version; promotion to
   `@champion` is explicit; challenger can shadow-score first (stretch).

**Honesty stance:** short-horizon reaction prediction is genuinely hard;
near-coin-flip accuracy is an acceptable *finding*. The rubric grades the
loop and the MLOps discipline, not alpha. This continues v1's
"honesty-as-a-feature" narrative, which the reviews confirmed is its
strongest presentation asset.

---

## 6. Airflow DAGs

| DAG | Schedule | Contents |
|---|---|---|
| `daily_pipeline` | 09:30 SGT | poll EDGAR index → fetch docs → 4 silver parsers → price ingest → dbt build (cosmos task group) → event detection → stage-1/2 inference (all horizons) → alerts. Per-task retries + freshness gate. Missed runs fire on scheduler start. |
| `daily_eval` | **12:00 SGT** | backfill actuals for closed horizons → per-horizon prediction-vs-actual metrics → drift check (frozen baseline) → degradation alert. |
| `retrain` | weekly + manual trigger | rebuild training set → train both stages → MLflow register → (shadow) → promote. |
| `nlp_overlay` | weekly + manual | 10-K retrieve → LLM extract → resolve → embed → gold-set regression score. |

---

## 7. Rubric & lecture coverage map

- **End-to-end pipeline (30):** multi-source ingestion → bronze → silver →
  dbt gold → events → serving, all idempotent/resumable. ✔
- **ML and real-time output (30):** batch processing + two-stage ML + the
  predict/compare/eval/alert loop + live dashboard fed by Kafka fast path +
  FastAPI model serving from registry. ✔ (v1's weakest criterion, now the
  centerpiece)
- **Technical depth & robustness (10):** retries/backoff, rate limiting,
  checkpoints, idempotency, dedup, pydantic validation, dbt tests, freshness
  + drift monitoring, parse-log auditability, pytest suite. ✔
- **Presentation (30):** narrative = problem → architecture → two deep-dives
  (Airflow DAG with a deliberately failed-and-retried task; the
  prediction-vs-actuals loop on the dashboard) → honest results → reflection.

Lecture topics honestly used: Day 1 (API ingestion, cleaning, SQL, serving),
Day 2 (retries, rate limits, pagination, checkpoints, idempotency, FastAPI,
**Airflow**, **Docker Compose**, **Parquet** price lake), Day 3 (**Kafka**,
**Flink** event-time windows + watermarks, **dbt** star schema/tests/lineage,
SCD2; Spark deferred with argument), Day 4 (**MLflow** tracking + registry
aliases, model serving, drift/skew taxonomy, canary/shadow discussion,
retraining strategy).

---

## 8. Build sequence (deadline 14 Sep; ~6 weeks of work + buffer)

| # | Phase | Contents | Est. |
|---|---|---|---|
| 0 | Foundations | repo restructure, psycopg_pool, pydantic config, pytest + parser fixtures, Compose skeleton (postgres/airflow/mlflow) | ~4 d |
| 1 | Multi-source ingestion | Source interface, document/entity schema migration, universe table, port EDGAR source, **market-data source** (yfinance → Parquet → Postgres) | ~5 d |
| 2 | Transform + dbt | port parsers, incremental facts, staging tests, price + event-study marts | ~4 d |
| 3 | Events & alerts | SCD2 diff → `filing_event`, rule engine → `alert` | ~3 d |
| 4 | ML core | stage-1 port, event-study labels, stage-2 model, prediction/actuals loop, frozen-baseline drift, registry aliases | ~6 d |
| 5 | Airflow | 4 DAGs, cosmos dbt group, retries, gates | ~4 d |
| 6 | Streaming | EDGAR feed → alerts → Postgres; Flink windows → sink → dashboard; durable dedup | ~3 d |
| 7 | Serving | FastAPI + live dashboard (reuse SVG components), Dockerize app | ~5 d |
| 8 | NLP hardening | pct-in-quote verification, gold-set curation (**owner task** — needs human judgment on 33 filings), harness as gate | ~3 d |
| 9 | Presentation | video script, slides, resume bullets, demo choreography (incl. staged task-failure retry demo) | ~4 d |

Owner-only tasks: curating the gold set labels; recording the video.

---

## 9. Out of scope for v2 (documented as SaaS roadmap)

Multi-tenancy/auth, hosted deployment, whole-market universe (thousands of
filers — needs concurrent ingest workers behind a shared token bucket),
non-US registries, Spark at whole-market scale, embedding-assisted entity
resolution. Each gets a slide-worthy "how we'd scale" paragraph instead.
