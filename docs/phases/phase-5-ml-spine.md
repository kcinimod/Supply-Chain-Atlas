# Phase 5 — The ML Core: a Filter, a Forecaster, and the Loop Between Them

> **Learning write-up & presentation notes.** v1 had one model (the 8-K
> substance classifier) and called it the ML spine. v2 rebuilds this into a
> **two-stage ML core**: stage 1 *filters* filings into material events, stage
> 2 *predicts the market's reaction* to each event at four horizons — and the
> genuinely graded part is the **predict → compare-with-actuals → evaluate →
> alert loop** that runs daily.
>
> **What this phase demonstrates (course rubric):** the rubric's literal
> examples — *"generate predictions … and compare earlier predictions with
> actuals once the actual data becomes available"* and *"evaluate the
> prediction model every day"* — plus MLflow tracking + **registry aliases**
> (`@champion`), a challenger promotion policy (with the canary/shadow
> discussion), **data-drift monitoring against a frozen baseline** (Day-4 drift
> taxonomy), training-serving **skew** prevention, and a retraining strategy
> that never trains on the daily tick.

---

## 1. What this phase is, in one breath

The graph answers *structural* questions; the event layer (phase 7) turns state
into discrete changes; the ML core answers the product question: **"when this
change lands, what happens to the stock?"**

```
filing_event ──▶ stage 1: 8-K substance classifier (the FILTER)
                   score attached to material_8k events (eightk_prediction)
             ──▶ stage 2: market-reaction model (reaction_clf)
                   P(abnormal return > 0) per horizon: 3d · 1w · 1m · 3m
                   ├─ prediction logged AT EVENT TIME  (reaction_prediction)
                   ├─ actuals backfilled as bars close each window
                   ├─ per-horizon metrics appended daily 12:00 SGT
                   └─ degradation / drift alerts → the same alert table users see
```

**Honesty stance, stated up front:** short-horizon reaction prediction from
public filings alone is genuinely hard, and stage 2's test AUC is
**near-coin-flip (~0.52)**. That is a *finding*, not a failure to hide — the
rubric (and any real investment-signal product) grades the measurement loop and
the MLOps discipline around it, which is where v2 spent its effort.

---

## 2. Stage 1 — the 8-K substance classifier, now a real filter

The v1 model survives with its design intact — it was good — but its *job*
changed: it no longer terminates in a metrics table; it **feeds the event
layer**.

- **Self-supervised labels from Item codes** (2.02 earnings, 5.02 officer
  changes → material; 7.01/8.01 catch-alls → routine-by-default, the stated
  grey zone). Not circular: labels come from the *code*, features from the
  *text*, and the printed "Item N.NN" headers are scrubbed from the body so the
  model can't read the label off the page. **17,521 labelled 8-Ks (~63/37).**
- **TF-IDF (1–2-grams) + logistic regression**, time-ordered 80/20 split — the
  holdout is the future. Latest champion: **ROC-AUC 0.989, F1 0.969** (n =
  2,800/700). The honest caveat stands: the primary 8-K document is templated,
  so some lift is structural; the harder benchmark (Exhibit 99.1 press-release
  text) remains documented future work.
- **The production path (new):** `ml score` (`model.score_new()`) scores every
  stored 8-K body the current champion hasn't seen and writes
  `eightk_prediction`; the `material_8k` event detector attaches the latest
  score to each event (`details.clf_proba`), where it becomes both a dashboard
  field and a **stage-2 feature**. v1 only ever scored inside train/eval —
  there was no inference path; now scoring runs in the daily DAG.
- **Eval renamed honestly.** v1 logged its recent-slice re-score under
  `split='live'`. The labels are Item-code-derived — known at labeling time —
  so this is a *health check on fresh filings*, not outcome-based truth. v2
  stores it as `split='recent'` and says so. The outcome-based loop is stage 2.

## 3. The registry — aliases and a promotion policy *(Day 4, exactly)*

v1 resolved "the model" as `max(version)` — no promotion concept, no rollback.
v2 (`ml/registry.py`) serves every consumer through **`models:/<name>@champion`**:

- Training registers a **challenger** version and records its test metric as a
  version tag.
- **`maybe_promote`** moves the `@champion` alias only if the challenger's
  metric is **at least as good** as the incumbent's recorded metric (a NaN
  metric — degenerate test slice — never promotes). A bad weekly retrain
  cannot silently take over serving.
- **Rollback = move the alias back.** No redeploy, no code change — the Day-4
  pitch for aliases, live.

*The canary/shadow discussion (what we rejected, for now):* a full **shadow
deployment** — challenger scoring everything alongside the champion with its
predictions logged-but-unserved — is the natural next step and the
`reaction_prediction` schema already keys by `model_version`, so shadow rows
coexist naturally. At one laptop and one user, a **canary** (traffic split) is
meaningless; the metric-gated promotion is the honest scale-appropriate
version of the same idea.

## 4. Drift — reworked against a frozen baseline *(fixing a v1 flaw)*

> *"v1's drift check compared the score distribution of a recent slice against
> the tail of the same live corpus — self-referential: as the corpus drifts,
> the baseline drifts with it, and the check goes blind. v2 freezes the
> baseline at train time."*

At every training run the champion's **train-slice score histogram** is
persisted to `ml_drift_baseline` (10 bins, keyed by model + version). The daily
drift check scores a **bounded recent slice** (400 docs) and computes **PSI**
against that frozen distribution — a true "training data vs now" comparison,
the **data drift** half of Day 4's drift taxonomy. (**Concept drift** — the
text→materiality relationship changing — is what the stage-2 outcome loop and
hit-rate alerts watch for; being able to name which check watches which drift
is the talking point.) Thresholds: WARN ≥ 0.20 (raises a dashboard alert),
FAIL ≥ 0.25 (also **fails the eval DAG task** — a gate, not v1's cosmetic WARN).

---

## 5. Stage 2 — the market-reaction model *(new, and the centerpiece)*

For every detected event, predict whether the company's **cumulative abnormal
return vs SPY** over each horizon will be positive.

**Design decisions (each one defensible on camera):**

- **ONE registered model (`reaction_clf`); horizon is a categorical feature.**
  Adding a 6-month horizon is an `INSERT INTO reaction_horizon`, not a new
  model, a new alias, or a schema change. Horizons are **data**:
  `3d/1w/1m/3m = 3/5/21/63 trading days`. *Rejected:* one model per horizon —
  four registry entries, four promotion decisions, and horizons frozen in code.
- **Labels are event-study CARs.** Sum of daily abnormal returns
  (`fact_price_daily`) over exactly N trading days after the event; a label
  exists **only when the window fully closed** (exactly N bars — a half-open
  window would leak a partial outcome). Trading days, not calendar days —
  weekends aren't information.
- **No training-serving skew, by construction.** Features come from one SQL
  query (`reaction_store.event_features`) used verbatim at train time and
  predict time. Features: event type, sector module, side, horizon
  (categorical) + stage-1 `clf_proba`, pre-event 5d/21d CAR, 21d volatility,
  volume ratio (numeric). Each prediction row also **freezes its feature values
  in JSONB** — the Day-4 prediction log, so skew is debuggable after the fact.
- **Time-ordered split on event date** (test = the future), gradient-boosted
  trees (shallow HGB) — enough capacity for feature interactions, no epochs to
  babysit on CPU.

**The honest result:** test **ROC-AUC 0.519**; per-horizon test hit rates
**0.50–0.55** (3d 0.503 → 3m 0.551) against a 0.529 base rate, on 18,116 train
/ 6,016 test (event × horizon) rows. Near-coin-flip. *The market does not hand
out easy money for reading public filings slightly faster* — and a system that
measures itself honestly enough to show that is the product.

## 6. The loop — what actually gets graded

1. **Predict at event time** (daily 09:30 DAG, right after detection): the
   champion scores every new event at all horizons →
   `reaction_prediction(event_id, horizon, model_version)`, immutable once
   written. **24,340 prediction rows** logged.
2. **Actuals backfill** as daily bars close each window — one set-based UPDATE
   per horizon fills `actual_car`/`actual_up`. **24,132 backfilled** so far.
3. **Daily 12:00 SGT evaluation** computes per-horizon trailing-180d hit rate
   and Brier score, appends `reaction_eval_metric` (a queryable model-health
   time series) and logs to MLflow. Current trailing hit rates: **3d 54%, 1w
   56%, 1m 57%, 3m 56%.**
4. **Degradation alert** fires into the user-visible `alert` table when a
   horizon's hit rate drops below 45% with n ≥ 30 — model health is a product
   signal, not a log line.
5. **Weekly retrain** (Sunday DAG) fields a challenger; promotion is the
   registry policy above.

**The trailing-eval caveat (say it before the examiner does):** today's
trailing hit rates (54–57%) beat the test-split hit rates (~50–55%) partly
because the trailing window still contains events the champion *trained on* —
history was scored in bulk at bootstrap. This is a property of the bootstrap,
not a leak in the loop: from now on every prediction is made at event time
before its outcome exists, so the metric becomes genuinely out-of-sample as
the window rolls forward. The eval is the same computation either way; only
the interpretation matures.

## 7. MLflow is now a real server

v1 tracked into a local sqlite file. v2 runs **`mlflow server` in Compose,
Postgres-backed, port 5000** — a multi-client registry the Airflow tasks, CLI,
and serving path all resolve against (`MLFLOW_TRACKING_URI`; the sqlite file
remains the offline fallback so the CLI works with the stack down). Registry +
aliases + experiment history survive any container restart because the state
lives in the same Postgres the warehouse does.

---

## 8. The tool ledger

| Tool | Verdict | Justification |
|---|---|---|
| **scikit-learn** | ✅ | TF-IDF+LogReg (stage 1), HGB (stage 2) — right-sized, CPU-only, interpretable where it matters. |
| **MLflow server + registry aliases** | ✅ | Tracking, versions, `@champion` promotion/rollback — the Day-4 pattern, not decoration: every consumer loads through the alias. |
| **Frozen-baseline PSI drift** | ✅ | Data-drift gate that can fail a DAG; baseline frozen at train (v1's self-reference fixed). |
| **Shadow / canary** | ◐ discussed | Schema supports shadow scoring by version; canary is meaningless at this scale — the promotion gate is the honest equivalent. |
| **Deep models / embeddings** | ⛔ deferred | The bottleneck is signal, not model capacity — AUC 0.52 with honest features won't be rescued by a bigger encoder. Documented upgrade path via pgvector. |
| **PyFlink in-stream ML** | ⛔ | Events arrive at batch cadence; scoring is a daily task. Streaming ML pays off only for stateful event-time inference we don't have. |

---

## 9. How to run

```bash
uv run python -m ml items            # Item codes -> label layer
uv run python -m ml train            # stage 1: train, register, maybe-promote
uv run python -m ml score            # stage 1 production scoring -> eightk_prediction
uv run python -m ml eval             # stage 1 recent-slice health (split='recent')
uv run python -m ml drift            # PSI vs frozen baseline (exit 1 on breach)

uv run python -m ml reaction-train   # stage 2: train reaction_clf, maybe-promote
uv run python -m ml reaction-predict # score new events at all horizons
uv run python -m ml reaction-eval    # backfill actuals + append metrics + alerts

# in the running system these are owned by daily_pipeline (predict),
# daily_eval (12:00 loop) and weekly_retrain (training) — MLflow UI: :5000
```

---

## 10. What I'd do with more time

- **Shadow-score the challenger** for a week before promotion (the schema
  already permits it) — then the promotion gate compares *live* metrics, not
  test-split metrics.
- **Richer stage-2 features**: filing-text embeddings (pgvector), event
  clustering, earnings-calendar proximity — chasing 0.55 honestly rather than
  0.52.
- **Classify the Exhibit 99.1 press release** — stage 1's harder, header-free
  benchmark.
- **Magnitude buckets**, not just direction — a 3-class target ({<−2%, ±2%,
  >+2%}) is closer to what a trading desk would ask.

---

*Key files: `ml/model.py` (stage 1: train/eval/score/drift), `ml/registry.py`
(@champion + maybe_promote), `ml/reaction.py` + `ml/reaction_store.py` (stage
2 + the loop SQL), `ml/config.py` (label rule, thresholds),
`db/migrations/010_reaction_ml.sql` (`reaction_prediction`,
`reaction_eval_metric`, `ml_drift_baseline`), `airflow/dags/daily_eval.py`,
`airflow/dags/weekly_retrain.py`, `docker-compose.yml` (mlflow service).*
