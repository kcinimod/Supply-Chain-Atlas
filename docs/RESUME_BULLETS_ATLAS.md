# Supply Chain Atlas v2 — résumé bullets

3–5 résumé-ready bullets (this is also the PDF submission content). Indented
sub-points are interview elaboration, not for the résumé. Every metric is
real and reproducible (see README / docs/phases).

---

## Primary set (pick 3–5)

- **Built an end-to-end investment-signal platform over SEC EDGAR and market
  data** — multi-source ingestion → dbt star schema → change-detection event
  layer → two-stage ML → FastAPI/live dashboard, orchestrated by **Airflow 3**
  in Docker Compose — processing **205,000+ filings and ~196,000 daily price
  bars** across a 79-entity universe defined as *data, not code*.
  - Survives a nightly 9-hour power-off: restart policies + latest-missed-run
    scheduling + cursor-based CDC ingestion make gaps self-heal.

- **Closed a genuine prediction-vs-actuals MLOps loop**: a multi-horizon
  market-reaction model (3d/1w/1m/3m, horizons config-driven) logs predictions
  at event time, backfills realized abnormal returns as bars arrive, appends
  per-horizon hit-rate/Brier daily (**24,300+ predictions, 24,100+ measured
  outcomes**), and auto-alerts on champion degradation — with MLflow registry
  **@champion alias promotion** (challenger promoted only if test metric holds)
  and PSI drift checks against a **frozen train-time baseline**.
  - Honest finding: ~0.52 test AUC — near coin-flip — reported as such; the
    engineered value is the self-measuring loop, not inflated accuracy.

- **Turned warehouse state into signals**: SQL/SCD2-diff detectors emit
  discrete change events (new/changed supply edges, 5%/10% stake crossings,
  insider clusters, material 8-Ks) that a rule engine converts into
  **6,000+ deduplicated, severity-graded alerts** served live to a dashboard
  with change badges and per-horizon model outlook.

- **Engineered resilient CDC ingestion + a real streaming fast path**:
  cursor-based EDGAR polling under SEC's 10 req/s ceiling (rate-limited,
  exponential-backoff retries, idempotent upserts, learned per-source
  freshness gates), plus live filings → **Kafka (Redpanda)** → alert consumer
  and trades → **Flink SQL event-time windows (watermarks)** → Postgres →
  dashboard, with durable dedup and self-resubmitting jobs.

- **Extracted supplier→customer edges from 10-K free text with a local LLM**
  (Qwen 2.5 via Ollama, schema-constrained JSON, retrieval + entity
  resolution + pgvector), hardened with a **fabrication guard** (an extracted
  percentage survives only if it appears verbatim in the model's quoted
  source) and a gold-set regression harness gated in the weekly Airflow DAG.

- **Raised engineering quality to production bar**: 64-test pytest suite over
  pure parsers that surfaced **5 real parsing bugs** (namespace-prefixed XML,
  legacy cover-page regexes, comma-grouped numerics), pooled DB connections,
  staging + marts dbt tests (82 green build steps), incremental
  materializations, and full Docker Compose packaging (Airflow, MLflow
  server, API, streaming workers).

---

## Condensed 3-bullet version (space-constrained résumé)

- Built an end-to-end investment-signal platform on SEC EDGAR + market data
  (multi-source CDC ingestion → dbt star schema/SCD2 → change-event detection
  → two-stage ML → FastAPI live dashboard), orchestrated by **Airflow 3** in
  Docker Compose; **205K+ filings, 196K price bars, 6K+ real alerts**.
- Closed a real prediction-vs-actuals loop: multi-horizon (3d–3m) market-
  reaction model with **24K+ predictions scored against realized abnormal
  returns**, daily evaluation DAG, degradation alerting, MLflow **@champion**
  promotion policy and frozen-baseline PSI drift gates.
- Shipped a Kafka/Flink fast path (live EDGAR feed → alerts; event-time VWAP
  windows → Postgres) and a local-LLM 10-K extractor with a pct-fabrication
  guard + gold-set regression gate; 64-test suite caught 5 real parser bugs.

---

## Tech tags

`Python` · `PostgreSQL` · `pgvector` · `dbt` · `Airflow 3` · `astronomer-cosmos` ·
`Kafka (Redpanda)` · `Flink SQL` · `MLflow` · `scikit-learn` · `yfinance` ·
`Parquet` · `FastAPI` · `Ollama / local LLM` · `Docker Compose` · `pytest` ·
`uv` · `SEC EDGAR`
