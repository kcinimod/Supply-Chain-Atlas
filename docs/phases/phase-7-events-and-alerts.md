# Phase 7 — Events & Alerts: Turning State Into Signal

> **Learning write-up & presentation notes.** The v2 layer that reframes the
> whole product: the warehouse knows *state* (who owns whom, who supplies
> whom); this layer detects *changes* in that state, and a rule engine turns
> changes into the alerts the dashboard — and the reaction model — run on.
> v1 had neither: its "alerts" were four hardcoded HTML rows.
>
> **What this phase demonstrates (course rubric):** the rubric's literal
> *"detect unusual X and trigger an alert"* example; **idempotency via
> natural-key dedup** (including a genuinely obscure Postgres feature);
> separation of detection from notification policy; and pipeline-health
> alerting (drift, degradation, stale sources) landing in the same
> user-visible channel as product alerts.

---

## 1. What this phase is for

v1's story was "turn EDGAR into a relationship graph." v2's story is **"detect
changes and turn them into investable signals"** — and this layer is the pivot:

```
gold layer (dbt marts, SCD2)  ──▶  detectors (events/detect.py)  ──▶  filing_event
                                                                        │
                                              rule engine (events/rules.py)
                                                                        ▼
             stream fast path (phase 4) ─────────────────────────▶   alert
             system health (drift, degradation, freshness) ──────▶   alert
                                                                        │
                                     reaction model predicts on events; ▼
                                     dashboard feeds, badges, Changes view
```

An *event* is a discrete, dated fact ("stake crossed 5%"). An *alert* is a
policy decision about that fact ("this deserves `high`"). Keeping them as two
tables with two modules is deliberate: detection logic changes when the *data*
changes; severity policy changes when the *product* changes. *(Rejected: one
merged table — every severity re-think would rewrite history or mix concerns.)*

---

## 2. The four detectors

All in `events/detect.py`, all reading the **gold** layer (detectors consume
marts, not silver — they benefit from every dbt test upstream):

| Event | Detector logic | Thresholds (module constants) |
|---|---|---|
| `material_8k` | Item-code label says material; the stage-1 classifier's latest score is attached (`details.clf_proba`) via lateral join on `eightk_prediction` | — |
| `supply_edge_new` | a *named* customer appears in a supplier's filing history for the first time | — |
| `supply_edge_changed` | same named edge, concentration moved vs the prior 10-K | ≥ 2pp |
| `stake_change` | consecutive SCD2 stake versions (`prev.valid_to = cur.valid_from`) crossed a regulatory level or moved materially | 5% / 10% levels; ≥ 2pp move |
| `insider_cluster` | ≥ N distinct insiders open-market buying (code P, acquired) one company in-window | N = 3 |

Two design points worth the camera:

- **The SCD2 payoff.** `stake_change` diffs `fact_ownership_stake`'s
  window-function history — the Phase-2 modeling decision (versioned stakes
  with `valid_from`/`valid_to`) is what makes "crossed 5%" a one-join query.
  History modeling wasn't decoration; it was the detector's substrate.
- **The classifier meets the event layer.** `material_8k` events carry the
  stage-1 probability, which flows to the dashboard *and* becomes a stage-2
  feature — the two-stage ML core is stitched together right here.

## 3. Idempotency — generous windows, natural keys

Detectors scan a **trailing window** (default 30 days — comfortably wider than
any laptop-off gap; `--window-days 3650` replayed all of history at bootstrap)
and upsert on the natural key:

```sql
UNIQUE NULLS NOT DISTINCT (event_type, cik, event_date, accession_no)
```

`NULLS NOT DISTINCT` (migration `009`) is the load-bearing detail: an
`insider_cluster` event has **no** accession (it summarises many filings), and
under SQL's default NULL semantics two NULL-keyed rows never conflict — the
constraint would silently not dedupe exactly the event type that needs it
most. *(Rejected: a sentinel `''` accession — it would corrupt the FK to
`raw_filing`.)*

Because inserts are conflict-skipping, **overlapping scans are no-ops** — the
window can be generous instead of precise, which is what makes the nightly
outage harmless. Per-detector isolation returns `-1` on failure, and the DAG
task raises on any negative — one broken detector neither hides nor takes the
others down.

## 4. The rule engine — severity as stated policy

`events/rules.py` derives one alert per un-alerted event, through an
investment-signal lens (each rule is a readable `if` block, not config
sprawl):

- `supply_edge_new` → **high** (a new disclosed dependency is directly
  actionable);
- `supply_edge_changed` / `stake_change` → **watch**, escalating to **high**
  on ≥ 5pp moves or a 10% crossing;
- `insider_cluster` → **watch**, **high** at ≥ 5 insiders;
- `material_8k` → **info** (frequent; awareness, not action — the classifier
  score is shown so users can rank within the tier).

Every alert carries a **unique `dedup_key`** (rule + cik + date + accession),
so re-runs never re-notify, and a **`source` discriminator** (`batch` |
`stream` | `system`): the Kafka fast path (phase 4) and the batch detector can
both see the same filing without double-alerting, and it's honest in the UI
about which path was first.

**`system_alert()`** is the hook the rest of the platform reports through —
classifier drift (WARN/FAIL), reaction-model degradation, stale sources all
land in the *same table users watch*. Pipeline health is a product signal,
not a log line.

---

## 5. What we built and verified (the numbers)

Replaying detectors over the full backfilled history (`--window-days 3650`):

| | count | severity mix |
|---|---|---|
| `material_8k` | 5,645 | info |
| `stake_change` | 433 | 328 watch · 105 high |
| `supply_edge_new` | 7 | high |
| `insider_cluster` | **0** | — |
| **events → alerts** | **6,085 → 6,085** | dedup verified: re-run inserts 0 |

**The zero is honest and diagnosable:** cluster detection needs breadth of
Form-4 *silver* coverage, and only a bounded slice of the 86k Form-4 filings
has been parsed so far — three distinct insiders buying the same company
simply don't co-occur in a thin sample (insiders mostly *sell*, and code-P
open-market buys are the rarest transaction type). The detector's logic is
exercised in isolation; the number will move when the silver backlog is
parsed. Reporting `0` beats loosening the threshold until something fires —
a threshold tuned to produce signal *is* the false-positive machine the
severity policy exists to prevent.

---

## 6. How to run

```bash
uv run python -m events detect --window-days 3650   # replay history (idempotent)
uv run python -m events alerts                       # derive alerts for new events
uv run python -m events run                          # both; totals printed
# scheduled: detect_events -> derive_alerts inside daily_pipeline (09:30 SGT)
```

---

## 7. What's next / with more time

- **Parse the Form-4 backlog** so `insider_cluster` has a real base to fire on.
- **Watchlists per user** — severity policy is global today; a SaaS would make
  thresholds tenant data (the `reaction_horizon` pattern, reused).
- **Alert fan-out** to email/Slack off the existing `atlas.alerts` topic —
  an output adapter, deliberately deferred until a human needs paging.

---

*Key files: `events/detect.py` (4 detectors), `events/rules.py` (severity
policy + `system_alert`), `events/store.py` (natural-key upserts),
`events/__main__.py` (CLI), `db/migrations/009_events_alerts.sql`
(`filing_event`, `alert`, `reaction_horizon`, the `UNIQUE NULLS NOT DISTINCT`
key), `airflow/dags/daily_pipeline.py` (detect → score/predict → alerts).*
