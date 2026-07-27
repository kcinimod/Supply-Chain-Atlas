# Phase 3 — Orchestration: Airflow in Compose, and What the Port Cost

> **Learning write-up & presentation notes.** How the pipeline runs itself in
> v2 — **Apache Airflow 3 in Docker Compose**, four DAGs, real retries, real
> gates — and the honest ledger of what switching *away* from Dagster lost and
> gained. The rubric rewards justified choices over tool-count, so the "why we
> switched" is graded as much as the "what".
>
> **What this phase demonstrates (course rubric):** the two literal demo
> examples — *"Airflow DAG: show when the workflow runs, task dependencies, and
> how failed tasks are retried"* and *"Docker Compose: show which services are
> started together"* — plus scheduling under a hard availability constraint,
> per-task retries with exponential backoff, and quality gates that fail runs.

---

## 1. What this phase adds

Phases 1–2 could run the whole pipeline **by hand**. Orchestration makes it a
**scheduled graph that runs itself, retries itself, and gates itself**:

```
daily_pipeline (09:30 SGT)
  sync_universe ─▶ poll_edgar ─▶ fetch_documents ─▶ 4 × parse_* ─┐
                                └────────────────▶ label_8ks ────┤
  ingest_prices ─────────────────────────────────────────────────┤
                                                                  ▼
                              freshness_gate ─▶ dbt_build (cosmos task group)
                                                                  ▼
                             detect_events ─▶ [score_8ks · reaction_predict] ─▶ derive_alerts
```

The pipeline was **built orchestration-ready** back in phases 1–2 — idempotent
upserts, resumable cursors, per-item error isolation, non-zero exit on hard
failure — which is precisely why v2 could swap the orchestrator itself without
rewriting a single business-logic function. That *design-for-orchestration* is
the point to make on camera.

---

## 2. The v2 decision: Airflow, after actually running Dagster

v1 chose Dagster and defended it well (asset-orientation matched the graph,
`dagster-dbt` stitched one lineage, native on Windows). v2 ported to
**Airflow 3.3 in Docker Compose**. Both statements are true, and saying both is
the strongest version of this slide: *we ran both and switched for stated
reasons*, not fashion.

**Why the switch:**

- **The rubric demos are Airflow-shaped.** Two of its four named examples are
  literally an Airflow DAG with retried tasks and a Compose stack. Airflow-in-
  Compose hits both with zero narrative risk in front of a grader who teaches
  Airflow.
- **The port was cheap because v1 did the hard part.** All business logic lives
  in orchestrator-agnostic idempotent module functions; the Dagster assets were
  3–5-line wrappers, and so are the Airflow tasks (`airflow/dags/*.py` — each
  task body is an import plus a call).
- **Retries are a net gain.** v1 declared no RetryPolicy on any asset. In v2
  *every* task inherits `retries=2` with `retry_delay=3min`, **exponential
  backoff**, capped at 15min (`atlas_common.DEFAULT_ARGS`) — so the rubric's
  "show how failed tasks are retried" demo is real behaviour, not staged.
- **Operational uniformity.** Airflow was already going to run in Compose for
  deployment reasons; running the scheduler where everything else lives beats a
  host-side `dagster dev` process the nightly shutdown kills ungracefully.

**What we lost — honestly:**

- **Unified source-stitched lineage.** Dagster's killer feature here was one
  asset graph from EDGAR to `fact_ownership_stake`, with dbt sources merged
  onto the Python assets that produce them. Airflow shows *task* dependencies;
  the data-level lineage now lives in dbt's own DAG plus discipline in task
  ordering. **astronomer-cosmos** recovers most of it — the dbt project renders
  as a task group with one task per model *and its tests*, so dbt failures are
  visible at model grain in the Airflow UI — but the Python→dbt seam is
  convention again, not metadata.
- **First-class asset checks.** Dagster's check primitive (drift, freshness,
  coverage as typed pass/warn/fail on an asset) became plain tasks that raise.
  Semantically equivalent, less discoverable in the UI.

*What we rejected:* running both (two schedulers, one laptop — operational
theater), and cron (no retries, no UI, no dependency graph — the things being
graded).

---

## 3. Scheduling around a laptop that sleeps *(the constraint that shaped everything)*

The machine is **off 00:00–09:00 SGT nightly**. Rules derived from that:

- **No schedule sits inside the off window.** `daily_pipeline` 09:30,
  `daily_eval` 12:00, `weekly_retrain` Sun 10:00, `nlp_overlay` Sat 10:00 —
  all Asia/Singapore-aware (`pendulum` timezone on every DAG).
- **Missed runs self-heal.** Every service is `restart: unless-stopped`, so the
  scheduler returns when Docker Desktop does; with `catchup=False` Airflow then
  fires the **latest missed run** of each DAG (one, not a backlog); and because
  ingestion is cursor-based CDC, that single run re-walks *every* missed EDGAR
  index day and widens the price window over the gap. **Recovery is a property
  of the data design, not an operator playbook.**
- **`max_active_runs=1`** everywhere — the steps are idempotent, but the SEC
  rate limiter is global, so overlapping runs would just queue on it.

> *"The interesting design question wasn't 'how do I schedule this' but 'what
> happens to Tuesday's run when the machine slept through Tuesday'. The answer
> is: Wednesday's run is Tuesday's run — the cursor makes them the same run."*

The self-healing claim above is load-bearing, and on **2026-07-27** two separate
production failures showed what it costs when a link in it is wrong. Both are
documented rather than quietly patched, because both are the kind of bug that
only appears once a stack runs unattended for a fortnight:

1. **The cursor advanced over unread days** — a silent 146-filing loss caught by
   the freshness gate, not by any test. Full write-up in
   [phase-1-ingestion.md](phase-1-ingestion.md) § 6d.
2. **Cosmos rendered the dbt project on every DAG import** (below).

### Rendering cost: why 49 tasks died before running a line
Cosmos's default `LoadMode.AUTOMATIC` shells out to `dbt ls` **at DAG-import
time**, which cost ~8.5s here against Airflow's stock 30s `DAGBAG_IMPORT_TIMEOUT`
— only ~3.5× headroom. The trap is that **Airflow 3 re-imports the DAG file
inside every task process**, so a 49-task run paid that cost ~49 times and had 49
independent chances to exceed it. When it did, the task did not fail with a
useful error; it failed with `Dag not found during start up` before executing
anything, then exhausted its startup reschedules. That is what killed the
2026-07-24 `daily_pipeline` and 2026-07-26 `weekly_retrain` runs — and only the
two Cosmos DAGs died, while `daily_eval` and `nlp_overlay` (0.5–0.7s parses) ran
through the same window untouched.

The fix has two halves, because speed alone would have been the wrong lesson:

- **Render from a prebuilt manifest.** `RenderConfig(load_method=DBT_MANIFEST)`
  reads `dbt/target/manifest.json` instead of spawning `dbt ls`. `dbt parse`
  regenerates it in `airflow-init` on every `compose up`, and a
  `refresh_dbt_manifest` task refreshes it ahead of the build, so DAG shape
  tracks the models. If the manifest is ever missing, `atlas_common` falls back
  to the live `dbt ls` path — a stale-shape risk is worse than a slow parse.
- **Raise the ceilings anyway** (`DAGBAG_IMPORT_TIMEOUT=180`,
  `DAG_FILE_PROCESSOR_TIMEOUT=240`). Cosmos still spends ~4.9s converting 97
  manifest nodes into a task graph, so the honest gain is 8.4s → 5.2s, *not* the
  sub-second the manifest mode suggests. The real win is headroom: ~3.5× → ~25×.

> *The generalisable point: a timeout that a healthy run clears by 3× is not a
> safety margin, it's a scheduled outage waiting for a slow morning. And when
> the thing being timed runs once per task rather than once per DAG, measure it
> per task.*

## 4. The four DAGs

| DAG | Schedule (SGT) | What it owns |
|---|---|---|
| `daily_pipeline` | 09:30 daily | universe sync → EDGAR poll → docs → 4 silver parsers ∥ prices ∥ 8-K labels → **freshness gate** → dbt build (cosmos) → event detection → stage-1 scoring + reaction predictions → alerts |
| `daily_eval` | 12:00 daily | backfill reaction actuals + per-horizon metrics → stage-1 recent-slice health → **drift gate** (frozen baseline; PSI breach fails the task) → freshness *alerts* |
| `weekly_retrain` | Sun 10:00 | refresh labels → train stage 1 → train stage 2 → score backlog ∥ `dbt build --full-refresh` |
| `nlp_overlay` | Sat 10:00 | fetch 10-K bodies → Ollama health check → LLM extract → embed → gold-set regression gate |

Design points worth saying out loud:

- **Training never runs on the daily tick.** v1's `selection="*"` retrained the
  model every day — a stated anti-pattern, fixed by giving training its own
  weekly DAG. Promotion is the registry's challenger policy (phase 5), so even
  a bad weekly retrain can't silently take over serving.
- **Gates are tasks that fail.** The freshness gate *hard-fails* the daily run
  before dbt builds on stale data (v1's check could only WARN); the drift gate
  fails the eval DAG on PSI breach *and* writes a dashboard alert first. A red
  task an examiner can see beats a log line nobody reads.
- **Eval at 12:00, three hours after ingest** — so today's bars and filings are
  in before predictions are compared with actuals. This DAG *is* the rubric's
  "evaluate the model every day at 8:00 AM" example, time-shifted to a slot the
  laptop is guaranteed awake.
- **The NLP DAG fails loudly when Ollama is down** — an LLM dependency should
  degrade with a clear message, not silently produce zero facts.

---

## 5. The deployment story: one image, solved twice *(Docker Compose demo)*

`airflow/Dockerfile` is small but carries two real war stories:

- **pip's resolver gave up** (`resolution-too-deep`) on airflow + mlflow + dbt
  + sklearn — a genuinely large dependency graph. **uv** resolves it in
  seconds; `apache-airflow` is pinned *inside* `requirements.txt` so no
  resolution can ever drift the base install.
- **The image's active interpreter is the airflow user's venv** at
  `/home/airflow/.local`, not `/usr/local` — uv must target that python or
  packages land somewhere the scheduler never looks. (Found the empirical way.)

The repo is **bind-mounted at `/opt/atlas`**, not baked in — code edits apply
on container restart; only dependency changes rebuild. The same image is reused
as the runtime for the serving API and the streaming consumers (phase 8) — one
Python environment to maintain instead of four.

Compose starts, together: Postgres (+pgvector), an init job that creates the
Airflow/MLflow databases, the MLflow server, three Airflow services (scheduler,
dag-processor, api-server) and the API — plus the streaming profile's services
when asked. That is the "which services start together and how they're
configured" demo, verbatim.

---

## 6. The tool ledger, revisited

| Tool | v1 verdict | v2 verdict |
|---|---|---|
| **Dagster** | used (core) | **replaced** — kept in the story as a run-both comparison |
| **Airflow 3** | rejected (Windows friction) | **used (core)** — in Docker, where the friction was |
| **dbt** | used | used — now via **cosmos** task groups, incremental models |
| **Kafka / Flink** | "in reserve" | **used** — the fast path earned them (phase 4) |
| **Spark** | rejected with a scale caveat | still deferred — workload is laptop-sized and rate-limited (I/O-bound); the argument is presentation material |

---

## 7. How to run

```bash
docker compose up -d          # postgres · mlflow · airflow (scheduler/dag-processor/api) · api
# Airflow UI: http://localhost:8080  — trigger daily_pipeline, watch a task retry
# Everything the DAGs call also runs identically from the CLI, e.g.:
uv run python -m ingestion poller
uv run python -m events run
```

---

## 8. What's next

The batch spine now runs itself. Phase 4 puts the **streaming fast path**
beside it (and makes its outputs land somewhere real); phases 5 and 7 fill in
the ML core and event layer the daily DAG already invokes.

---

*Key files: `airflow/dags/atlas_common.py` (retries, SGT, cosmos config),
`airflow/dags/daily_pipeline.py`, `airflow/dags/daily_eval.py`,
`airflow/dags/weekly_retrain.py`, `airflow/dags/nlp_overlay.py`,
`airflow/Dockerfile` + `airflow/requirements.txt` (the uv story),
`docker-compose.yml` (the stack).*
