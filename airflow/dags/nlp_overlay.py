"""Supply-chain NLP overlay — Saturdays 10:00 SGT (and manual trigger).

    fetch new 10-K bodies ─> LLM extract (Ollama on the host) ─> embed
                                                              └─> gold-set score

Weekly because 10-Ks are annual documents; extraction is idempotent (the
extract log skips done filings) so most runs are cheap no-ops. The Ollama
health check fails fast with a clear message when the host model isn't up —
an LLM dependency should degrade loudly, not silently produce nothing.
"""
from __future__ import annotations

from airflow.decorators import dag, task

from atlas_common import DEFAULT_ARGS, START


@dag(
    dag_id="nlp_overlay",
    schedule="0 10 * * 6",
    start_date=START,
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["atlas", "nlp"],
)
def nlp_overlay():

    @task
    def fetch_10k_bodies() -> int:
        from ingestion import fetch_docs
        return fetch_docs.run(limit=40, form_prefix="10-K") or 0

    @task
    def extract() -> dict:
        from nlp import ollama_client, pipeline
        if not ollama_client.health():
            raise RuntimeError(
                "Ollama is not reachable (host.docker.internal:11434) — start it "
                "on the host or skip this run")
        return pipeline.run_extract()

    @task
    def embed() -> dict:
        from nlp import pipeline
        return pipeline.run_embed()

    @task
    def gold_score() -> dict:
        """Regression gate: score the live table against the curated gold set.
        Skips quietly while the gold set is still the 1-line seed."""
        import json
        from pathlib import Path
        gold = Path("/opt/atlas/nlp/gold/supply_gold.jsonl")
        n_lines = sum(1 for line in gold.open(encoding="utf-8")
                      if line.strip()) if gold.exists() else 0
        if n_lines < 10:
            return {"skipped": f"gold set has {n_lines} rows; curate it first"}
        from nlp.gold import score
        rc = score.main(["--model", "live"])
        if rc not in (0, None):
            raise RuntimeError(f"gold-set score exited {rc}")
        return {"scored": True, "gold_rows": n_lines}

    fetch_10k_bodies() >> extract() >> embed() >> gold_score()


nlp_overlay()
