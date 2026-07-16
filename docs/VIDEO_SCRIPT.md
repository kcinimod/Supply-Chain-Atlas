# Supply Chain Atlas v2 — video walkthrough script (~15 min)

Companion to the slide deck and résumé bullets (`RESUME_BULLETS_ATLAS.md`).
Each section: **[SAY]** the spoken line, **[SHOW]** what's on screen. Times are
cumulative targets — aim for 14–15 min. The two graded demos (Airflow retries,
the prediction-vs-actuals loop) get the most time — protect them.

---

## 0:00 — Hook (45s) · slide 1

**[SAY]** "A small semiconductor supplier's annual report quietly disclosed
that one customer — Applied Materials — was 58% of its revenue. That single
sentence is an investment signal: this company's fate is chained to one buyer.
Supply Chain Atlas finds those sentences — and every other relationship
*change* in SEC filings — automatically, every day. And then it asks the
harder question: can we predict how the market reacts? And it measures its own
answer."
**[SHOW]** Slide 1 (title), then the dashboard company view of UCTT — the
supply edge + the 4 outlook chips.

## 0:45 — Problem & requirements (1.25 min) · slide 2

**[SAY]** "Public filings tell you who depends on whom — insider trades,
ownership stakes, subsidiaries, customer concentration. But it's scattered
across a dozen filing types at about two thousand filings a day, mostly as
messy text. The user is an analyst watching a supply-chain universe. The
product loop is: detect a relationship change, alert on it, predict the
market reaction over four horizons, then — when the actual prices arrive —
score the prediction. To stay laptop-sized but realistic I scoped to a
79-entity hardware/semiconductor universe — and the universe itself is data,
one CSV, so rescoping never touches code."
**[SHOW]** Slide 2 — problem + the detect → alert → predict → measure loop.

## 2:00 — Architecture (2.5 min) · slide 3

**[SAY]** (walk left to right, one sentence per layer)
"Ingestion is a pluggable source registry — two real sources today: SEC EDGAR,
polled through a cursor-based CDC pattern with rate limiting, retries and
checkpoints, plus daily market bars that land as a Parquet bronze lake before
Postgres. Silver is four fixture-tested Python parsers. Gold is a dbt star
schema — conformed dimensions, five edge facts, two flavors of SCD Type 2,
eighty-two build steps with tests at both staging and marts. On top of the
warehouse sits the event layer — detectors that diff state into discrete
change events, and a rule engine that turns events into severity-graded,
deduplicated alerts. The ML core is two stages — a filter and a reaction
model. There's a genuine Kafka-and-Flink fast path for live filings and
trades. Serving is FastAPI plus a live dashboard. And Airflow 3 runs the whole
thing daily inside Docker Compose."
**[SHOW]** Slide 3 (architecture diagram from the README).

## 4:30 — Deep-dive #1: Airflow — schedule, dependencies, retries (3 min) · live demo

**[SAY]** "This is the daily pipeline at 9:30 Singapore time: poll EDGAR,
fetch documents, four parsers in parallel, then a freshness gate — a data
quality check with per-source thresholds *learned* from each source's
historical filing gaps — then dbt as a task group where every model and every
test is its own task, then event detection, model scoring, predictions,
alerts."
**[SHOW]** Airflow UI, `daily_pipeline` graph view, zoom the dbt group.

**[SAY]** "One constraint shaped the orchestration: my laptop is off from
midnight to 9am. Everything restarts with Docker; Airflow fires the latest
missed run on wake; and because ingestion is cursor-based, the poller
re-walks exactly the index days it slept through. Gaps self-heal — no
operator, no manual backfill."
**[SHOW]** `ingest_checkpoint` table; compose file `restart: unless-stopped`.

**[SAY]** "And failures retry: every task has two retries with exponential
backoff. Here's a task log showing a retry after a transient network error."
**[SHOW]** A task-instance log with retry lines. (Optionally: stop Postgres
mid-run, show fail → retry → succeed.)

**[SAY]** "Training deliberately never runs on this daily DAG — a weekly DAG
retrains and a separate 12-o'clock DAG evaluates. That separation is next."

## 7:30 — Deep-dive #2: the prediction-vs-actuals loop (3.5 min) · live demo

**[SAY]** "Stage one: an 8-K materiality classifier — labels are derived from
the filing's own Item codes, features are the body text with the printed item
codes scrubbed out so it can't cheat, and the split is time-ordered so the
test set is the future. AUC .99. It's versioned in MLflow, and serving loads
the *champion alias* — a retrain only moves that alias if the challenger's
test metric is at least as good. Rollback is moving a pointer."
**[SHOW]** MLflow registry, `eightk_substance_clf` with the @champion tag.

**[SAY]** "Stage two is the reaction model. For every detected event it
predicts whether the abnormal return — versus the S&P — will be positive at
three days, one week, one month, three months. Horizons are rows in a config
table: adding a six-month horizon is an INSERT, not a refactor. The
prediction row is written at event time with its features frozen — actuals
are NULL. Then every day at noon, once a horizon's trading-day window closes,
the eval DAG backfills the realized abnormal return and appends hit-rate and
Brier to a metric time series. Twenty-four thousand predictions; twenty-four
thousand measured outcomes; trailing hit rates 54 to 57 percent by horizon."
**[SHOW]** `reaction_prediction` row with NULL actuals → the same row filled;
the per-horizon hit rates on the dashboard pulse strip.

**[SAY]** "And here's the honest part. The model's test AUC is point-five-two
— near coin-flip. If I told you I predict markets from public filings at 80%
accuracy, you shouldn't believe me. The value is that the system *measures
itself*: if the champion's trailing hit rate drops below 45%, it raises an
alert — automatically, into the same alert feed users see. Drift is checked
the same way — PSI against a score distribution *frozen at training time*,
not against a moving target — and a breach fails the DAG red."
**[SHOW]** The degradation alert rule in code; the drift task.

## 11:00 — Streaming fast path (1.5 min) · live demo

**[SAY]** "Batch covers the day; the fast path covers right now. A producer
polls EDGAR's live feed and publishes every new filing to a Kafka topic —
deduplicated durably, so restarts never replay. A consumer matches filings
against the universe and lands alerts in the same table the dashboard reads —
a live filing shows up in the UI within a poll tick. In parallel, a trade
stream flows through Flink SQL — event-time windows with watermarks — into
ten-second VWAPs that land in Postgres: that's the live market strip."
**[SHOW]** Redpanda console (topics), Flink UI (running job), a
`stream`-badged alert on the dashboard rail, the market strip.

## 12:30 — Serving & the dashboard (1 min) · live demo

**[SAY]** "Serving is FastAPI — the dashboard bootstraps the graph once and
polls a live endpoint every 30 seconds; the 'live' dot is real and goes amber
if a poll fails. Companies with recent change events carry delta badges;
changed supply edges are emphasized; each company shows its four-horizon
model outlook; and the Changes view is the product in one screen — every
relationship change, newest first, click-through to the graph."
**[SHOW]** Change badges → outlook chips → Changes view → click into a company.

## 13:30 — Results (45s) · slide 4

**[SAY]** "In numbers: two hundred five thousand filings and two hundred
thousand daily bars across two sources; eighty-two green dbt build steps; six
thousand real alerts across five event types; two registered models behind
champion aliases; twenty-four thousand predictions with measured outcomes; a
sixty-four-test suite that caught five real parser bugs; and a Compose stack
that survives being switched off nine hours a night."
**[SHOW]** Slide 4 (verified numbers).

## 14:15 — Reflection (45s) · slide 5

**[SAY]** "Hardest part: closing the actuals loop idempotently — event dates,
trading-day windows, late bars, and a machine that sleeps nightly, all
converging to the same state. Proudest of: the system's honesty — the
reaction model is near-coin-flip and the system says so, daily,
automatically; a dashboard that flatters you is worthless. With more time:
curate the NLP gold set that's already gated in the DAG, widen the universe
to thousands of filers with concurrent ingestion, shadow-score challengers on
live events before promotion, and bring in Spark when the universe goes
whole-market."
**[SHOW]** Slide 5, then close on the dashboard Changes view.
