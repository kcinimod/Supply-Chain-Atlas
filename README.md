# Supply Chain Atlas

**Detect supply-chain and ownership *changes* in regulatory filings — and turn
them into investable signals.**

An end-to-end data-engineering + MLOps platform over SEC EDGAR and market
data: multi-source ingestion, a dimensional warehouse, a change-detection
event layer, a two-stage ML core with a genuine prediction-vs-actuals loop,
a Kafka/Flink fast path, and a live graph dashboard — orchestrated by Airflow,
packaged in Docker Compose.

> **Why.** Public markets disclose a huge amount about *who depends on whom* —
> a supplier gaining or losing a >=10% customer, an activist stake crossing 5%,
> insiders cluster-buying — but it's scattered across a dozen filing types,
> mostly as messy text. The v1 project built the graph; **v2 builds the
> product**: every relationship *change* becomes an event, an alert, and a
> multi-horizon market-reaction prediction whose accuracy is measured against
> what actually happened.

Built solo. Scoped to a curated **79-entity universe** across 5
hardware/semiconductor sectors (universe is *data*, not code — one CSV edit to
rescope) so it stays laptop-sized while exercising every stage of the
lifecycle.

---

## Architecture

```
                       SOURCES (pluggable registry — ingestion/sources.py)
     ┌────────────────────────┬──────────────────────────┬────────────────────┐
     │ EDGAR backfill + CDC   │ Market bars (yfinance)   │ [future: news, …] │
     └──────────┬─────────────┴───────────┬──────────────┴────────────────────┘
                ▼                         ▼
  BRONZE   gzipped docs on disk      Parquet lake (dt-partitioned)      idempotent, cursor-resumable
                ▼                         ▼
  SILVER   4 tested parsers → Postgres    price_daily
                ▼
  GOLD     dbt star schema — conformed dims, 5 edge facts, SCD2 (snapshot +
           window fn), incremental big facts, staging+marts tests (82 steps)
                ▼
  EVENTS   change detectors → filing_event   ·   rule engine → alert (REAL)
                ▼
  ML       stage 1: 8-K substance classifier (the filter)
           stage 2: market-reaction model — P(abnormal return > 0) at
           3d / 1w / 1m / 3m (horizons are DB rows, extendable) —
           predictions logged at event time → actuals backfilled as bars
           arrive → daily 12:00 SGT eval → degradation alerts
           MLflow server: registry + @champion alias promotion policy
                ▼
  SERVING  FastAPI (graph · live feed · alerts · changes · outlook · pulse)
           + live dashboard (polling, change badges, per-horizon outlook)

  FAST PATH  EDGAR live feed → Redpanda → alert consumer → alert table (+ topic)
             trades → Flink SQL (event-time, watermarks, 10s VWAP) → sink → Postgres

  ORCHESTRATION  Airflow 3 (Docker Compose, LocalExecutor, Asia/Singapore):
    daily_pipeline 09:30 · daily_eval 12:00 · weekly_retrain Sun 10:00 ·
    nlp_overlay Sat 10:00 — per-task retries, cosmos dbt task group,
    freshness gate, drift gate vs FROZEN train baseline
```

**Laptop-off resilience** (the machine sleeps 00:00–09:00 SGT nightly): every
service is `restart: unless-stopped`; on wake, Airflow fires each DAG's latest
missed run, the EDGAR poller's day-cursor re-walks the gap, the price window
widens to cover it, and the Flink submit-supervisor re-submits the streaming
job. Gaps self-heal — no operator action.

---

## The graph — 5 edge types on one conformed star schema

| Edge | Source filing | From → To | Mart |
|---|---|---|---|
| Insider transaction | Form 4 | insider (person) → company | `fact_insider_transaction` |
| Institutional holding | 13F-HR | manager → security (CUSIP) | `fact_holding` |
| Parent / subsidiary | 10-K Exhibit 21 | company → subsidiary | `bridge_subsidiary` |
| Beneficial-ownership stake | Schedule 13D/G | owner → company | `fact_ownership_stake` (SCD2) |
| **Supply-chain** | 10-K free text (local LLM) | supplier → customer | `fact_supply_relationship` |

Plus the v2 additions: `fact_price_daily` (daily abnormal returns vs SPY),
`filing_event` (detected changes), `alert`, `reaction_prediction`
(the prediction/actuals log), `reaction_eval_metric` (model health time series).

## The event layer — state → signal

| Event | Detector logic | Typical alert |
|---|---|---|
| `supply_edge_new` | named customer appears in a 10-K for the first time | high |
| `supply_edge_changed` | concentration moved >= 2pp vs prior 10-K | watch/high |
| `stake_change` | 13D/G stake crossed 5%/10% or moved >= 2pp (SCD2 diff) | watch/high |
| `insider_cluster` | >= 3 distinct insiders open-market buying in-window | watch/high |
| `material_8k` | Item-code label, classifier score attached | info |

Detectors are idempotent (natural-key unique) and scan a generous trailing
window, so overlapping runs and laptop-off gaps are harmless.

## The ML loop (the honest part)

Stage-2 predicts the *direction of the abnormal return* after each event, per
horizon. Test AUC is ~0.52 — **near coin-flip, and that's the finding**:
short-horizon reaction prediction from public filings alone is hard. What the
system demonstrates is the *loop* an investment-signal product needs:

1. prediction logged at event time (features frozen in the row — no
   training-serving skew, same SQL builds train and serve features),
2. actuals backfilled as the horizon's trading-day window closes,
3. per-horizon hit-rate/Brier appended daily at 12:00 SGT (MLflow + Postgres),
4. alert fired if the champion degrades; weekly retrain challenges it, and the
   registry's `@champion` alias only moves if the challenger's test metric is
   at least as good (rollback = move the alias back).

---

## Design choices (the justified part)

- **Airflow over Dagster (v2 change).** v1 ran Dagster; business logic was
  deliberately orchestrator-agnostic, so the port cost days, not weeks.
  Airflow adds per-task retries the v1 assets never had, and Airflow-in-
  Compose is the deployment story the rubric's demos are built around.
- **Postgres + pgvector, not a graph DB** — the star schema *is* the graph;
  one store gives SCD2, dbt serving, vector search, and the event tables.
- **Poll, don't stream, for batch ingest.** EDGAR publishes a daily index — a
  CDC cursor is the right pattern. Kafka/Flink earn their keep on the genuine
  live feed and event-time trade windows (which now land in Postgres and the
  dashboard — no more windows nobody reads).
- **A local, open-weights LLM (Ollama qwen2.5:3b)** for the supply-chain
  overlay: offline, free, schema-constrained JSON. v2 adds a **fabrication
  guard**: an extracted percentage survives only if the number appears
  verbatim in the model's own source quote.
- **Universe and horizons are data** (`db/seeds/universe.csv`,
  `reaction_horizon` table) — scaling scope or adding a 6-month horizon is an
  INSERT, not a refactor.
- **Spark deferred, documented.** The workload is laptop-sized and rate-
  limited (I/O-bound); Spark earns its keep at whole-market scale.

---

## Quickstart

Prerequisites: Docker Desktop, [`uv`](https://docs.astral.sh/uv/), and (for
the NLP overlay) [Ollama](https://ollama.com). Copy `.env.example` → `.env`.

```bash
docker compose up -d                     # postgres+pgvector · Airflow 3 · MLflow · API
uv sync && uv run python scripts/migrate.py

# one-time seed + history
uv run python -m ingestion load-companies
uv run python -m ingestion backfill
uv run python -m ingestion prices        # a decade of daily bars -> lake + Postgres
uv run python -m ingestion fetch-docs --form 8-K --limit 2500

# silver + gold
uv run python -m transform form4         # + form13f | exhibit21 | sched13dg
cd dbt && uv run dbt build --profiles-dir . && cd ..

# events + ML core
uv run python -m events run --window-days 3650
uv run python -m ml items && uv run python -m ml train
uv run python -m ml reaction-train && uv run python -m ml reaction-predict
uv run python -m ml reaction-eval        # backfills actuals, appends metrics

# from here the Airflow DAGs own the daily cycle:
#   daily_pipeline 09:30 SGT · daily_eval 12:00 SGT ·
#   weekly_retrain Sun 10:00 · nlp_overlay Sat 10:00
# UI: http://localhost:8080 (Airflow) · :5000 (MLflow) · :8000 (dashboard/API)

# streaming fast path (optional)
docker compose --profile streaming up -d # Redpanda · Flink · producers · sinks

# supply-chain NLP overlay (needs host Ollama)
ollama pull qwen2.5:3b && ollama pull nomic-embed-text
uv run python -m nlp extract && uv run python -m nlp embed
```

Tests: `uv run pytest` (parser fixtures, edgar client, NLP post-filters —
64 tests, no network/DB needed).

---

## Repo layout

```
ingestion/     Source registry: EDGAR (poller/backfill/docs) + market bars; universe-as-data
transform/     per-filing parsers -> silver (pure, fixture-tested)
dbt/           staging + marts star schema, tests, SCD2 snapshot, incremental facts
events/        change detectors -> filing_event; rule engine -> alert
ml/            stage-1 8-K classifier · stage-2 reaction model · registry aliases · drift
nlp/           supply-chain overlay: retrieve -> local-LLM extract (+ pct guard) -> resolve -> embed
streaming/     EDGAR live producer · alert consumer · trades · Flink SQL · window sink
api/           FastAPI serving layer (graph/live/outlook/health)
dashboard/     static/ front-end (bootstraps once, polls /api/live)
airflow/       Dockerfile + dags/ (4 DAGs, cosmos dbt task group)
db/            pooled connections, migrations 001..011, seeds/universe.csv
tests/         pytest suite (parsers, clients, NLP filters)
docs/          V2_REVAMP_PLAN.md · phases/ design write-ups · presentation assets
```

---

*SEC EDGAR data is public. This project is for education / data-engineering
demonstration; nothing here is investment advice.*
