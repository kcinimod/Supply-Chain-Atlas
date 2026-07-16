# Phase 1 — Ingestion: A Multi-Source Data Backbone

> **Learning write-up & presentation notes.** What the ingestion layer does in
> v2, why each decision was made, and the data-engineering concepts behind it —
> written so each concept doubles as a talking point for the assessment video.
>
> **What this phase demonstrates (course rubric):** API ingestion with rate
> limiting + retries/backoff (Day 2), incremental loading / CDC cursors +
> checkpoint/resume, idempotent upserts, a bronze data lake in two formats
> (gzipped documents + **Parquet**), source-freshness monitoring, and the
> Postgres-vs-alternatives storage decision (Day 1/3).

---

## 1. What this phase is for

Every pipeline starts the same way: **get the data, and never lose it.**

Phase 1 is the part of *Supply Chain Atlas* that talks to the outside world.
In v1 that meant one source — SEC EDGAR. v2 makes ingestion **multi-source**:
EDGAR filings *and* daily market bars, behind one small `Source` interface, each
with its own cursor, its own bronze layout, and the same reliability discipline.
Nothing is parsed, joined, or modelled yet. We just **collect reliably and
archive faithfully**, in a way that respects each provider's access rules and
can be re-run safely forever.

That humble job is the foundation everything else (the graph, the event layer,
the ML core, the dashboard) is built on — and in v2 it also has to survive a
hard operational constraint: **the laptop is off 00:00–09:00 SGT every night.**
Every design below is checked against "what happens after nine hours of
downtime?"

---

## 2. The universe is data now *(the biggest v1 → v2 change here)*

> *"In v1 the 79-company universe was a hardcoded Python dict — changing scope
> meant editing source code. In v2 it's a table seeded from a CSV: scope changes
> are data changes, and a future tenant is a row-set, not a code fork."*

The curated universe (79 entities, 5 hardware/semiconductor modules, suppliers
**and** their customers in-set so supply edges land on real nodes — see the v1
rationale, unchanged) now lives in:

- **`db/seeds/universe.csv`** — ticker, CIK (for private owners), title, module,
  side (`supplier` / `customer` / `owner`).
- **`universe_member`** (migration `008`) — the seeded table. Rows dropped from
  the CSV are **deactivated, never deleted** — history matters, and SCD-style
  soft deletion means old facts keep their context.
- **`ingestion/universe.py`** — `seed_from_csv()` (idempotent upsert),
  `resolve_and_apply()` (ticker → CIK once, from `company_tickers.json`), and
  accessors (`members()`, `supplier_tickers()`, `price_tickers()`) that every
  downstream consumer uses **instead of importing a dict**.

The `side` column earns its keep immediately: the NLP overlay reads only
`supplier` 10-Ks, the price source pulls every listed ticker, and 13F/13G logic
keys off `owner`. One seed file drives three pipelines.

## 3. The Source registry — the multi-source seam

`ingestion/sources.py` defines a tiny `Source` protocol (`name`, `description`,
`ingest(**kwargs) -> int`) and a registry:

| Source | What it does | Cursor |
|---|---|---|
| `edgar_poller` | incremental daily-index CDC over EDGAR | last processed index day |
| `edgar_backfill` | full submissions-API history per member | per-company progress |
| `market_prices` | daily OHLCV bars via yfinance | max ingested trade date |

> *"Orchestration and the CLI iterate the registry instead of knowing providers
> by name — adding a data source is: implement `ingest()`, register it, done.
> The market source was the proof: it reused the checkpoint table, the bronze
> discipline, and the per-item error isolation without touching EDGAR code."*

*What we rejected:* a full plugin framework with entry points and dynamic
discovery. Two real sources don't justify it; a dict of three named units is
honest about the current scale while still decoupling the callers.

---

## 4. The second real source: market bars → Parquet lake → Postgres

The reaction model (phase 5) needs prices, so v2 adds `ingestion/market.py`:

1. **Incremental window per run** — `(max(trade_date) in price_daily) + 1 .. today`.
   Reading the high-water mark from the *table* (not the checkpoint) means a
   manual DB restore self-corrects. A machine that slept for days simply gets a
   wider window — the laptop-off constraint handled by construction.
2. **yfinance batch download** — one session, all ~80 tickers + the benchmark
   (**SPY**, so abnormal returns have a market to be abnormal against).
3. **Bronze: a Parquet lake.** Each run's raw snapshot lands columnar and
   date-partitioned at `data/lake/prices/dt=YYYY-MM-DD/prices.parquet` (snappy).
   Same "raw first, derive later" ELT posture as the gzipped filing archive —
   but in the *analytics-native* format the lecturer teaches for lakes, because
   bars are tabular where filings are documents. **Two bronze formats, each
   fitting its data shape**, is itself a talking point.
4. **Silver: `price_daily`** — upsert on `(ticker, trade_date)`, so re-runs
   no-op. **~196k bars, 2015-01-02 → today**, refreshed incrementally daily.
5. **Checkpoint `market_prices` advances**; one delisted/renamed symbol is
   logged and skipped, never sinks the run (per-item isolation, as everywhere).

*What we rejected:* a paid market-data API (yfinance is free and daily-grain is
all the event-study needs) and streaming quotes into the batch layer (live
trades belong to the Kafka/Flink fast path — phase 4).

## 5. Connection pooling — fixing a real v1 flaw *(db/pool.py)*

> *"v1's `connection()` helper opened a fresh TCP + auth handshake per
> statement — invisible on 100 rows, painful on a 195k-filing backfill. v2
> swaps in `psycopg_pool` behind the exact same `with connection() as conn`
> contract, so every store module got pooling without changing a line."*

The pool is lazy (importing the module never opens sockets — tests stay
DB-free), bounded (`DB_POOL_MAX`, default 8), fails fast when the DB is down
(30s timeout), and keeps v1's read-only guard (`SET TRANSACTION READ ONLY` for
dashboard/API reads). Keeping the public API identical is the design point:
**fix the mechanism, not the callers.**

---

## 6. Key concepts carried forward (each one is a talking point)

The v1 EDGAR discipline survives unchanged — these are re-verified in v2, not
rewritten:

### 6a. The raw / bronze layer, and why we keep it *(Day 1 — data lake, ELT)*
`archive.py` writes each filing document **gzipped, immutable, date-partitioned**
(`data/raw/{form_type}/dt=YYYY-MM-DD/{accession}.gz`, sha256 recorded). Raw data
is the source of truth; everything downstream is derived and disposable. When a
Phase-2 parsing assumption turned out wrong, the fix was a re-fetch, not a
redesign — the ELT payoff, live.

### 6b. Respecting the SEC rate limit *(Day 2 — rate limiting)*
A **global thread-safe minimum-interval limiter** capped at 8 req/s (under SEC's
10/s fair-access ceiling) plus the mandatory descriptive `User-Agent`, in
`http_client.py`. This constraint is stronger and more real here than in most
projects — SEC actually blocks violators.

### 6c. Retry with exponential backoff — and knowing *what* to retry *(Day 2)*
Retry `{429, 500, 502, 503, 504}` with doubling waits (via `tenacity`); fail
fast on 401/404 — those fail identically every time, so retrying wastes
requests. Task-level retries in Airflow (phase 3) sit *on top* of this: the HTTP
layer handles transient blips, the orchestrator handles task-scale failures.

### 6d. Two ingestion paths: backfill vs poller *(Day 2 — incremental / CDC)*
History via the **submissions API** per company (32 years in a few calls);
new filings via the **daily-index poller with a date cursor** — change-data-
capture applied to filings. After any downtime the cursor **re-walks every
missed index day automatically**: this is the mechanism that makes the nightly
laptop-off window a non-event.

### 6e. Idempotency *(Day 2 — the property that makes re-runs and retries safe)*
Every filing write is `ON CONFLICT (accession_no) DO NOTHING` — the accession
number is EDGAR's globally-unique natural key. Verified: re-running the
backfill lands 0 new rows. Idempotency is *why* Airflow can retry any task
freely: running a step twice can never double-count.

### 6f. Checkpoint / resume & per-item error isolation *(Day 1/2)*
`ingest_checkpoint` records each source's cursor + last status; every loop
wraps each item in try/except so one malformed company or document is logged
and skipped. The full backfill finished **0 failures across 79 companies and
~195k filings** — but the isolation is there for when SEC hiccups.

### 6g. Postgres (+ pgvector) as the warehouse — a deliberate choice *(Day 1/3)*
> *"The project is a relationship graph, so I considered Neo4j — and DuckDB for
> analytics — but chose Postgres: one store gives relational integrity, SCD2,
> recursive traversal, pgvector for the NLP overlay, and a serving layer, with
> no extra infrastructure."*

The full DuckDB counter-argument from v1 stands (single-writer file lock vs a
continuously-ingesting multi-client system; no server for the API to connect
to; dbt-postgres maturity; pgvector) — and so does the honest counterpoint:
DuckDB *would* win for static, read-only exploration over the Parquet lake.
Different jobs, different tool.

### 6h. A real debugging story: 403, not 404 *(honest reflection)*
SEC's Archives return **403, not 404**, for a daily-index day that isn't
published yet. Fixed by treating 403/404 as "no index that day" — narrowly, so
a genuine block still surfaces. (Sibling bug: `company_tickers.json` lists
share classes, which mislabeled Boeing as `BA-PA`; universe members pin their
canonical seed ticker.)

### 6i. Source schema drift: the vanishing 13D/G *(monitoring / freshness)*
SEC's 2024-12-18 structured-data mandate renamed `SC 13G` → `SCHEDULE 13G`; the
form allow-list silently filtered the new spelling, and ownership filings
"stopped" for months. Caught by diffing max-filing-date per form; fixed by
accepting both spellings (**9,838** filings recovered). The lasting lesson is
now a standing guardrail — `ingestion/freshness.py`:

- **Logical sources, not raw form strings** — renamed spellings collapse into
  one source, so the rename itself doesn't false-alarm while a true stoppage
  still trips.
- **Data-driven per-source thresholds** — each source's tolerated quiet stretch
  is learned from its own inter-filing gap history (Form 4 alerts within a
  fortnight; 13F tolerates a quarter).

In v2 this check is a **hard gate task in the daily Airflow DAG** (a stale
source fails the run before dbt builds on bad data) *and* a softer alerting
task in the eval DAG that surfaces quiet sources to the dashboard. v1 ran it as
a Dagster asset-check; the check code didn't change, its enforcement got teeth.

---

## 7. What we built and verified (the numbers)

| Metric | Value |
|---|---|
| Filing metadata landed | **~195.6k** filings, 79 entities, **1994 → present** |
| Backfill failures | **0** (idempotent re-run: 0 new) |
| Ownership filings recovered by the rename fix | 9,838 |
| Daily price bars (`price_daily`) | **~196k**, 2015 → present, incremental |
| Price benchmark | SPY (for abnormal returns) |
| Universe members (`universe_member`) | 79 active, seeded from CSV |
| Bronze | gzipped docs (sha256) + Parquet price lake (`dt=` partitions) |

---

## 8. Design choices & honest limitations

- **Metadata now, documents on demand.** The backfill lands filing *metadata*
  (cheap, complete); full documents are fetched in bounded batches by
  `fetch-docs`. Phase 2 fetches exactly what it parses.
- **Scope is a seed file.** Widening the universe is a CSV edit + one command —
  and deactivation (not deletion) keeps history coherent.
- **yfinance is a convenience dependency.** It's an unofficial API; if it broke,
  the `Source` seam is exactly where a replacement provider slots in. The
  Parquet lake means history is never hostage to the provider.
- **Supply-chain edges are text, not structured.** EDGAR's structured forms give
  ownership/insider/subsidiary edges; supplier→customer lives in 10-K free text
  and is the NLP overlay (phase 6).

---

## 9. How to run

```bash
docker compose up -d                          # Postgres + pgvector (+ Airflow, MLflow)
uv run python scripts/migrate.py              # apply schema (001..011)
uv run python -m ingestion load-companies     # seed reference + universe_member
uv run python -m ingestion backfill           # 32 yrs history for all 79 members
uv run python -m ingestion prices             # a decade of bars -> lake + Postgres
uv run python -m ingestion fetch-docs --limit 200   # documents -> bronze
uv run python -m ingestion poller             # incremental; the daily DAG owns this
uv run python -m ingestion freshness          # standalone freshness check
```

---

## 10. What's next

**Phase 2 — Transform.** Parse the raw filings into silver tables (now with a
proper pytest fixture suite) and model the graph as a dbt star schema — which in
v2 also grows a price-return fact (`fact_price_daily`, abnormal returns vs SPY)
that the ML core's labels and actuals are measured in.

---

*Key files: `db/seeds/universe.csv` + `ingestion/universe.py` (universe-as-data),
`ingestion/sources.py` (Source registry), `ingestion/market.py` (bars → Parquet
lake → `price_daily`), `ingestion/http_client.py` (rate limit + retry),
`ingestion/backfill.py`, `ingestion/poller.py`, `ingestion/fetch_docs.py`,
`ingestion/freshness.py`, `ingestion/archive.py`, `db/pool.py` (psycopg_pool),
`db/migrations/001_init.sql`, `db/migrations/008_universe_prices.sql`.*
