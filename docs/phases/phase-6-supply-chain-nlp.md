# Phase 6 — The Supply-Chain NLP Overlay (the namesake)

> **Learning write-up & presentation notes.** The project's namesake edge:
> supplier→customer relationships that exist **nowhere** in EDGAR's structured
> data — only in 10-K prose. Extracted with a **local, open-weights LLM**
> (schema-constrained), post-filtered against fabrication, resolved to CIKs,
> and embedded into **pgvector**. Honest tiers, honest coverage, and — new in
> v2 — a guard born directly from a measured failure.
>
> **What this phase demonstrates (course rubric):** LLM-based relation
> extraction with **grounding/provenance checks** (a data-quality gate on model
> output), retrieval-before-extraction (mini-RAG), local embeddings + vector
> search, an evaluation harness wired in as a **regression gate** in the
> Airflow DAG, and honest partial-coverage reporting.

---

## 1. What this phase adds, and why it's hard

Every edge so far (Form 4, 13F, Exhibit 21, 13D/G) came from **structured**
data. Supplier→customer links surface only in **free text** — chiefly the 10-K
customer-concentration disclosure ("customer A accounted for 14% of our
revenue"), under Reg S-K / ASC 280. The universe was deliberately built so a
supplier's customer is usually in-set, so an extracted edge lands on a real
node.

The catch, and the honest thesis of the whole project: **the counterparty is
often not named.** Filers write "one customer accounted for 15% of revenue".
Extraction is **inherently partial**, and the pipeline records reliability
*tiers* rather than pretending every disclosure yields an edge. In v2 these
edges also gained a consumer: the `supply_edge_new` / `supply_edge_changed`
event detectors (phase 7) fire off this table — extraction quality now has
downstream stakes.

---

## 2. A local, open-weights LLM — the deliberate choice

Pulling `{customer, %}` out of messy prose is a relation-extraction task where
LLMs vastly outperform regex/NER. Rather than a hosted API, the extractor runs
**Qwen 2.5 (3B) via Ollama**, on-box:

- **Self-contained & offline** — no key, no per-call cost, filings never leave
  the machine (fitting, for a project about supply-chain integrity).
- **Schema-constrained decoding** — Ollama constrains generation to a JSON
  schema, so a 3B model *physically cannot* emit invalid JSON. The task is a
  short passage → a few fields; small is enough. Model id is one config
  constant.
- **Provenance kept** — every fact carries its `source_quote`. In v2 that quote
  stopped being decoration and became an *enforcement surface* (§4).

*Why not a hosted LLM:* marginal accuracy for a new dependency, cost, and
filings off-box. *Why not spaCy/regex:* brittle on varied phrasing for a much
larger tuning cost.

### 2b. I tested that choice — Qwen 2.5:3b vs Llama 3.2:3b

The model id being config, I swapped in `llama3.2:3b` and re-ran the same
10-Ks, same schema, same prompt (snapshots kept in
`supply_relationship_{qwen,llama}`):

| variant | facts | named | resolved | failures (full run) |
|---|---|---|---|---|
| Qwen 2.5:3b (terse prompt) | 65 | 9 | 3 | 0 |
| Llama 3.2:3b | 64 | 23 | 7 | **6 × 180s timeout** |
| Qwen 2.5:3b (few-shot prompt) | 47 | 12 | 5 | 0 |

- **Llama is ~6× slower on this CPU-only box and lost 6 of 33 filings to
  timeouts** — real data loss. Qwen stays the default; Llama's latency is a
  hardware problem, not a code one.
- **Few-shot prompting helped Qwen** (lead with the positive rule + two worked
  examples): named recall up, noise down, and it newly caught the genuine
  `ICHR → AMAT` and `ICHR → LRCX` edges.
- **But the A/B's sharpest finding was about fabrication:** on UCTT the filing
  says each top customer is *"more than 10%"* — no number — yet **every
  variant emitted a specific percentage** (Qwen 58.7/54.5, Llama 11.1). An
  edge-count can never surface this; only grounding or ground truth can.

## 3. Retrieval before extraction *(don't send a 400k-char 10-K to a 3B model)*

A 10-K is ~400k characters; 99% is irrelevant. `nlp/text.py` first **retrieves**
candidate passages — windows around a percentage that also mention a *customer*
and *revenue* — and only those few hundred characters reach the LLM. A
miniature RAG pattern: retrieve, then extract; small prompt, on-topic, fast.

## 4. Post-filters: tiers, and the fabrication guard *(v2's key addition)*

The LLM returns `{name, pct_of_revenue, named, quote}` per customer.
`nlp/extract.py` then applies three filters — the model proposes, the code
disposes:

1. **Placeholder demotion.** "Customer A", "one customer", "our largest
   customer" are not resolvable names → tier `unnamed`. A v2 bug fix with a
   name: the old regex demoted anything starting with "the …", which swallowed
   **"The Boeing Company"**; `ANON_RE` now demotes only "the *generic-noun*"
   forms ("the customer", "the U.S. government"). Found by the test suite,
   which is why the NLP filters are *in* the pytest suite.
2. **The pct-in-quote fabrication guard (`_grounded_pct`).** A percentage
   survives **only if its number appears verbatim in the model's own source
   quote** (10 matches "10", "10%", "10.0"). This is the direct answer to the
   UCTT case from the A/B: the filing says "more than 10%", the model invents
   58.7 — the guard keeps the *edge* and kills the *invented number*
   (`pct = NULL`, honestly unknown). One regex, zero model changes, and the
   known-worst failure mode is structurally impossible.
3. **Dedup by customer name** within a filing. Repeated mentions of the same
   counterparty collapse to the first fact — keyed by *name*, not
   (name, pct), because the guard can null a repeat's pct and a value-sensitive
   key would leak duplicates back in.

Each surviving fact lands in one tier — the honest counterpart of the
dashboard's solid-vs-dashed edges:

| Tier | Meaning | Graph treatment |
|---|---|---|
| `resolved` | named + matched to an in-universe CIK | a **real** supplier→customer edge |
| `named_unresolved` | a real company, not in-universe | name-only node |
| `unnamed` | concentration with no counterparty | a fact with no edge |

(Also kept from v1: a filer naming *itself* is rejected —
`customer_cik == supplier_cik` produced a Thermo Fisher self-edge once.)

## 5. Entity resolution & embeddings *(unchanged in role, re-verified)*

`nlp/resolve.py` normalises names (lowercase, strip Inc/Corp/… suffixes) and
matches `dim_company`, helped by a small alias map for household names.
Unmatched names stay name-only — honest partial coverage.

Each disclosure passage is embedded locally (`nomic-embed-text`, 768-dim,
Ollama) into pgvector (`supply_embedding`, 52 rows). Nearest-neighbour cosine
search finds concentration disclosures with **no keyword overlap** — the query
"a single large customer accounts for most of our revenue" surfaces the right
passages by *concept*. This is why the warehouse was pgvector from Phase 1.

## 6. The gold set — from monitored to evaluated, wired in as a gate

The UCTT fabrication proved that edge-counts can't measure precision. v2 built
the harness (`nlp/gold/`): `dump_candidates.py` pre-fills a review file with
the **union of every model variant's extractions** (33 filings' worth, tagged
by proposer, with retrieved passages) for a human to curate into
`supply_gold.jsonl`; `score.py` reports **edge precision/recall + percentage
accuracy** per tier, using the same name-normaliser as production
(apples-to-apples).

In v2 the scorer is a **task in the weekly `nlp_overlay` DAG** — a regression
gate on the live table. It **skips quietly while the gold set is below 10
rows** (today it's a 1-line seed: the UCTT filing, labeled with `pct: null`
precisely because the true disclosure has no number). Curating the remaining
33-filing review file needs human judgment on filings — an explicit **owner
task**, tracked, not forgotten. The gate's design point survives its empty
state: when a model/prompt swap regresses precision, the DAG goes red, not the
graph quietly wrong.

---

## 7. What we built and verified (honest numbers)

Current live table: **71 customer-concentration facts** — **57 unnamed / 9
named-but-out-of-universe / 5 resolved** in-universe edges, all textbook
supply-chain relationships:

| Supplier → customer | % of supplier revenue | note |
|---|---|---|
| UCTT → AMAT (Ultra Clean → Applied Materials) | 58.7% | |
| UCTT → LRCX (Ultra Clean → Lam Research) | 54.5% | |
| ICHR → AMAT (Ichor → Applied Materials) | 38% | caught by the few-shot prompt |
| ICHR → LRCX (Ichor → Lam Research) | 38% | caught by the few-shot prompt |
| RKLB → LMT (Rocket Lab → Lockheed Martin) | 16% | |

**The low resolved count is the finding, not a failure** — direct evidence for
the thesis that supply-chain graphs can't be built from public filings alone.
The overlay surfaces what *is* disclosed and is honest about the rest. The
extraction lifecycle runs as the Saturday `nlp_overlay` DAG (with an Ollama
health check that fails loudly when the host model is down), and the resolved
edges now generate `supply_edge_new` events → **high-severity alerts** (7 so
far) in phase 7.

---

## 8. The tool ledger

| Tool | Verdict | Justification |
|---|---|---|
| **Ollama + Qwen 2.5 (3B)** | ✅ | Local relation extraction; schema-constrained JSON reliable at 3B; A/B-tested against Llama 3.2 and kept. |
| **Grounding post-filters** | ✅ | `_grounded_pct` + placeholder demotion + name dedup — data-quality gates on *model output*, the same posture as dbt tests on data. |
| **nomic-embed-text + pgvector** | ✅ | Local embeddings → semantic search over disclosures. |
| **Gold-set harness as DAG gate** | ✅ (awaiting curation) | Measurement, not vibes; skips until the owner labels the 33 filings. |
| **Hosted LLM API** | ⛔ | Marginal gain; key/cost; filings off-box. |
| **spaCy / regex NER** | ⛔ | Too brittle on varied 10-K prose for the tuning cost. |
| **GPU** | ◐ optional | Both local models are GPU-capable; CPU-only here. The scale lever, not a requirement. |

---

## 9. How to run

```bash
ollama pull qwen2.5:3b && ollama pull nomic-embed-text

uv run python -m ingestion fetch-docs --form 10-K --limit 24
uv run python -m nlp extract           # retrieve -> LLM -> post-filters -> tiers
uv run python -m nlp embed             # local embeddings -> pgvector
uv run python -m nlp edges             # print resolved in-universe edges

uv run python -m nlp.gold.dump_candidates   # build the review file (33 filings)
uv run python -m nlp.gold.score             # score live table vs curated gold set
# scheduled: the nlp_overlay DAG, Saturdays 10:00 SGT
```

---

## 10. Honest limitations & what I'd do with more time

- **Coverage is low by nature** — most suppliers name no in-universe customer;
  more 10-Ks add a handful more edges, not hundreds.
- **The gold set is still a seed.** The single highest-leverage hour of human
  work in the project is labeling those 33 filings — it turns three "did it
  regress?" questions (model swap, prompt change, filter change) into numbers.
- **Grounding is necessary, not sufficient.** `_grounded_pct` kills invented
  numbers but can't catch a *wrong-but-present* number (a pct lifted from a
  neighbouring sentence). Only the gold set measures that.
- **Embedding-assisted resolution** — use the pgvector embeddings (not just the
  alias map) to match awkward name variants.
- **A larger local model** would lift recall on ambiguous phrasing — one config
  change, and the gold set is the yardstick that would justify it.

---

*Key files: `nlp/extract.py` (`_grounded_pct`, placeholder demotion, dedup),
`nlp/config.py` (`ANON_RE`, prompt, retrieval regexes), `nlp/text.py`
(retrieval), `nlp/resolve.py`, `nlp/gold/{dump_candidates,score}.py`,
`airflow/dags/nlp_overlay.py` (the DAG + gold gate),
`tests/test_nlp_extract_resolve.py`.*
