# Phase 2 — Transform: From Raw Filings to a Tested Graph

> **Learning write-up & presentation notes.** What the transform layer builds,
> why each decision was made, and the data-engineering concepts behind it —
> each concept doubles as a talking point for the assessment video.
>
> **What this phase demonstrates (course rubric):** cleaning/parsing messy
> source formats, a dimensional **star schema** with conformed dimensions and a
> factless bridge (Day 3), **SCD Type 2** two ways (snapshot + window function),
> dbt layering with **data-quality tests at staging *and* marts**, **incremental
> materializations**, and — new in v2 — a pytest fixture suite that found real
> parser bugs.

---

## 1. What this phase is for

Phase 1 collected filings and archived them faithfully. Phase 2 turns that
immutable pile of documents into a **queryable relationship graph** — the first
real *answers* the project can give: *who trades in whom, when, and how much.*

```
raw_filing + bronze docs            (Phase 1 landing)
   │
   ▼  Python parsers (transform/)   XML/HTML/SGML → typed rows    [pytest fixture suite]
form4_transaction · form13f_holding · exhibit21_subsidiary · sched13dg_stake   (silver)
   │                                                    price_daily (silver, Phase 1)
   ▼  dbt-postgres                  SQL modeling + tests + snapshots
dim_* / fact_* / bridge_*  (gold star schema)  +  SCD2  +  fact_price_daily
```

**Why this split (talking point):** Python does the messy, format-specific
parsing (every filing type has its own quirks); dbt does declarative SQL
modeling, data-quality testing, and slowly-changing-dimension history. Each tool
does the job it's best at, and the boundary between them is a clean, typed
table. The parsers' role is unchanged from v1 — what changed is that they are
now **provably** correct on their known quirks, because v2 wrote the tests v1
never had.

---

## 2. The parsers, and the test suite that audited them *(v2's big add)*

The four parsers are **pure functions** (`bytes → dataclasses`, no DB, no
network) — deliberately, because pure functions are cheap to test against
fixture filings. v1 shipped them with **zero project tests**; v2 built a
**64-test pytest suite** (`tests/`, fixtures included, no network or DB needed)
covering the parsers, the EDGAR client, and the NLP post-filters.

> *"Writing the fixture suite wasn't ceremony — it found five real bugs in code
> that had already processed hundreds of thousands of rows. That's the honest
> argument for tests: not that the code was bad, but that 'it ran fine so far'
> is not evidence of correctness on the format variant you haven't met yet."*

The five bugs, each a genuine SEC-format landmine:

1. **Comma-grouped numbers in Form 4** — some filers write `1,000` shares;
   `float("1,000")` raises. The share/price parser now strips grouping commas.
2. **Namespace-prefixed XML roots in 13F and 13D/G** — some vintages emit
   `<ns1:informationTable>`; the `_slice` helper that cuts documents out of SGML
   envelopes only matched bare tags, silently returning nothing.
3. **The 13D/G `%`-of-class regex vs cover-page row numbers** — the phrasing
   *"Percent of Class Represented by Amount in Row (11): 7.5%"* means the skip
   window must be able to cross the row *number*; `_PCT` now excludes only `%`
   (not digits) and is length-bounded so it can't wander onto the next item.
4. **Headers without a `FILED BY` block** — some 13D/G SGML headers omit it;
   the subject-block slice assumed it existed and mis-sliced (and a missing
   `</SEC-HEADER>` would have silently scanned one character — `find()`
   returning −1).
5. **The NLP anonymity regex ate real companies** — "The Boeing Company"
   matched the "the …" placeholder pattern and was demoted to *unnamed*.
   `ANON_RE` now demotes only "the *generic-noun*" forms (see phase 6).

*What we rejected:* end-to-end tests through Postgres. The parsers being pure
means unit fixtures give most of the value at none of the infrastructure cost;
integration behaviour is covered by the idempotent re-run property + dbt tests.

**Per-item isolation + audit (unchanged, still load-bearing):** each parser run
logs one row per attempted filing (`*_parse_log`) — success, empty, or error —
which makes selection **idempotent** (done filings never re-parse) and errors
**retryable** (a parser fix + re-run repairs prior failures automatically).

The hard-won domain knowledge in the parsers carries straight over from v1 and
is worth naming on camera: the XSL-rendered-HTML vs raw-XML trap (fixed by
reprocessing from source — the ELT payoff), the SGML `.txt` envelope fallback,
13F's *thousands-to-dollars* unit cutover at 2022-Q4, Exhibit 21's row-number
column, and the dual 13D/G eras (structured XML post-2024 mandate, cover-page
regex before — both resolving subject/filer CIKs from the SGML header).

---

## 3. The star schema *(Day 3 — dimensional modeling)*

> *"I modeled the graph as a star schema — central fact tables surrounded by
> conformed dimensions. That satisfies the dimensional-modeling teaching *and*
> is literally the relationship graph: each fact row is an edge, each dimension
> row is a node."*

| Table | Grain | Role |
|---|---|---|
| `fact_insider_transaction` | one Form 4 transaction | edge: insider → company *(incremental)* |
| `fact_holding` | one 13F holding-quarter | edge: manager → security *(incremental)* |
| `bridge_subsidiary` | one parent→subsidiary pair | **factless bridge** (name-only nodes) |
| `fact_ownership_stake` | one stake version | edge: owner → company (**SCD2** by window fn) |
| `fact_supply_relationship` | one disclosed customer | edge: supplier → customer (NLP, tiered) |
| **`fact_price_daily`** *(v2)* | one (ticker, trading day) | returns + **abnormal return vs SPY** |
| `dim_company` / `dim_person` / `dim_security` / `dim_date` | nodes + time | conformed dimensions |

The v1 modeling war stories still apply and still demo well: `in_universe` had
to become an **attribute** of `dim_company`, not a filter (a conformed dimension
must contain every node any fact references — caught by a dbt `relationships`
test, not luck); `dim_person` doubles as the entity-resolution layer (insider
CIK dedupe + name-variant counts); CUSIP→CIK resolution is **deferred with a
reason** (CUSIP is a licensed identifier; a fuzzy name join would forge edges).

### `fact_price_daily` — the event-study fact *(v2)*

One row per (ticker, trading day): daily return, benchmark (SPY) return, and
the **abnormal return** (`ticker − benchmark`) that every reaction label and
backfilled actual in the ML core is measured in, plus 20-day volume context
(`volume_ratio_20d`) for spike features.

The interesting engineering is the **incremental materialization**: bars only
append, but window functions need history. So each run **scans a 60-day
lookback and emits only the trailing 30 days** (`delete+insert` on `price_id`)
— the 30-day emit window always has ≥20 prior days in scan scope, so `lag()`
and the rolling average recompute correctly at the boundary. A `post_hook`
creates the `(ticker, trade_date)` index the ML core's lateral joins need.
*What we rejected:* full rebuilds (v1's default — fine at 1k rows, wasteful at
196k and growing daily) and computing returns in Python (set-based SQL over a
window is exactly dbt's wheelhouse).

The same incremental pattern (lookback window + `delete+insert`) now also
covers the two facts that grow forever, `fact_holding` and
`fact_insider_transaction`. And `dim_date` — the conformed day spine — was
widened to span **price** dates too, the "conformed dimensions extend, not
rebuild" lesson paying out a third time.

---

## 4. dbt: layering and tests — now at two altitudes *(Day 3 — data quality)*

Layers: **sources** (Python-loaded silver) → **staging** views (thin
cleaning/casting — now including `stg_price_daily` and `stg_universe_member`)
→ **marts** (the star schema).

> *"v1 tested only the marts. v2 adds tests at the **staging** layer
> (`_staging.yml`) so a broken silver load fails fast — before marts build on
> top of it. Same idea as validating at the source boundary in code: catch bad
> data at the first layer that can see it."*

- staging: `not_null`/`unique` on every silver key, `accepted_values` on
  `acquired_disposed ∈ {A, D}` and `side ∈ {supplier, customer, owner}`,
  `not_null` on price columns;
- marts: `unique`/`not_null` on dimension + surrogate keys, `relationships`
  (referential integrity) from fact FKs to dimensions, tier/value checks.

**`dbt build` runs models and tests together: 82 steps, all green.** A test
failure fails the build — and in v2 the build runs inside the Airflow DAG as a
cosmos task group, so bad data turns the pipeline red where the operator looks.

---

## 5. SCD Type 2 — versioned history, two ways *(Day 3 — SCD)*

Both v1 mechanisms survive unchanged, and the pairing is the talking point:

- **dbt snapshot** (`company_snapshot.sql`, `check` strategy on
  ticker/title/module) captures reference-data drift **going forward** — each
  change closes the old row (`dbt_valid_to`) and opens a new current one.
  Verified live with a ticker rename and revert.
- **Window-function SCD2** (`fact_ownership_stake`) reconstructs stake history
  **retroactively** in one pass — `valid_from` = the filing's as-of date,
  `valid_to` = `LEAD()` of the next, `is_current` = the open row — because
  13D/G amendments are already event-dated.

> *"A snapshot records change you can only observe run-over-run; a window
> function rebuilds history that's already dated in the data. Two SCD2
> situations, two tools."*

In v2 this SCD2 stake history stops being a display artifact and becomes an
**input**: the `stake_change` event detector (phase 7) diffs consecutive stake
versions (`prev.valid_to = cur.valid_from`) to find threshold crossings — the
warehouse's history model driving the product's change-detection.

---

## 6. What we built and verified (the numbers)

| Metric | Value |
|---|---|
| pytest suite | **64 tests** (parsers, EDGAR client, NLP filters) — 5 real bugs found & fixed |
| dbt build | **82 steps, all green** (models + staging & marts tests + snapshot) |
| Insider transactions | 1,388 parsed from 630 filings, 0 errors |
| Institutional holdings | 171,218 from 20 filings, 0 errors (7,188 CUSIPs) |
| Parent→subsidiary edges | 2,645 across 25 filers (15 legitimately no-exhibit) |
| Ownership stakes | 809 from 700 filings (771 on in-universe subjects) |
| Price fact | ~196k rows, abnormal returns vs SPY, incremental 60d-scan/30d-emit |
| SCD2 | snapshot (go-forward) + stake-history window fn (retroactive) |

Recognizable graph answers, unchanged since v1 and re-verified: State Street's
~$173B NVIDIA stake with a clean quarterly share time series, Musk 19.9% of
Tesla, Thermo Fisher's 1,270 subsidiaries, Ameriprise's Bloom Energy stake
tracked across amendments.

---

## 7. Design choices & honest limitations

- **Vertical slice first, then widen** (v1's method, kept): Form 4 proved
  parse→silver→star→SCD2→tests end-to-end; three more edge types reused the
  machine; v2's price fact reused it again.
- **CUSIP→company resolution stays deferred** — a data-availability wall, not
  effort: the only honest crosswalks are licensed. The moment one is available,
  `fact_holding` joins the same graph as the other edges.
- **13D/G legacy `%`-of-class stays partial** (~half of text-era rows);
  subject/filer always resolve via the SGML header. Same-date group filings
  still produce zero-width SCD2 windows.
- **Subsidiaries are name-only nodes** (private entities, no CIK); occasional
  mojibake and footnote bleed remain cosmetic.
- **Test fixtures are curated, not exhaustive.** 64 tests encode the quirks we
  *know*; the parse-log + idempotent re-run is the safety net for the quirks we
  don't.

---

## 8. How to run

```bash
uv run pytest                                        # 64 tests, no DB/network
uv run python -m transform form4      --limit 600
uv run python -m transform form13f    --limit 20
uv run python -m transform exhibit21  --limit 40
uv run python -m transform sched13dg  --limit 700
uv run dbt build    --project-dir dbt --profiles-dir dbt   # 82 steps
uv run dbt snapshot --project-dir dbt --profiles-dir dbt   # SCD2 company history
```

(In the running system, all of this is owned by the `daily_pipeline` DAG — the
weekly retrain DAG additionally runs `dbt build --full-refresh` to rebuild the
incremental facts against any restated history.)

---

## 9. What's next

The graph is state; the product is **change**. Phase 7 diffs this warehouse
into `filing_event` rows; the ML core (phase 5) predicts the market's reaction
to them; and `fact_price_daily` is the ruler those predictions are measured
with.

---

*Key files: `transform/form4.py`, `transform/form13f.py`, `transform/exhibit21.py`,
`transform/sched13dg.py` (parsers), `transform/pipeline.py`, `transform/store.py`,
`tests/` (fixture suite), `dbt/models/staging/_staging.yml` (staging tests),
`dbt/models/marts/fact_price_daily.sql` (incremental event-study fact),
`dbt/models/marts/_marts.yml`, `dbt/snapshots/company_snapshot.sql`.*
