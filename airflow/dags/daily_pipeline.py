"""The daily batch pipeline — 09:30 SGT (laptop reliably awake by then).

    universe/reference sync
        └─> EDGAR poll (CDC cursor) ──> fetch docs ──> 4 silver parsers ─┐
        └─> market prices (yfinance) ────────────────────────────────────┤
        └─> 8-K label layer ─────────────────────────────────────────────┤
                                                                          ▼
                       freshness gate ──> dbt manifest ──> dbt build (cosmos)
                                                                          ▼
                       detect events ──> [score 8-Ks · reaction predict] ─> alerts

If the laptop wakes after 09:30, the scheduler triggers the missed run on
startup (catchup=False keeps it to the latest one); the poller cursor then
re-walks every EDGAR index day it slept through, and the price window widens
to cover the gap — ingestion self-heals by design, not by operator action.
"""
from __future__ import annotations

from airflow.decorators import dag, task

from atlas_common import DEFAULT_ARGS, SGT, START, dbt_task_group


@dag(
    dag_id="daily_pipeline",
    schedule="30 9 * * *",
    start_date=START,
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["atlas", "batch"],
)
def daily_pipeline():

    @task
    def sync_universe() -> int:
        from ingestion import reference, universe
        reference.load_reference()
        universe.seed_from_csv()
        resolved, unresolved = universe.resolve_and_apply()
        if unresolved:
            raise RuntimeError(f"unresolved universe tickers: {unresolved}")
        return len(resolved)

    @task
    def poll_edgar() -> int:
        from ingestion import poller
        return poller.run()

    @task
    def fetch_documents() -> int:
        from ingestion import fetch_docs
        return fetch_docs.run(limit=None, form_prefix=None) or 0

    @task
    def ingest_prices() -> int:
        from ingestion import market
        return market.run()

    # transform.pipeline entry points: run (Form 4), run_13f, run_exhibit21, run_13dg
    _SILVER_FNS = {"form4": "run", "form13f": "run_13f",
                   "exhibit21": "run_exhibit21", "sched13dg": "run_13dg"}

    def _silver(name: str):
        @task(task_id=f"parse_{name}")
        def _run() -> dict:
            from transform import pipeline
            return getattr(pipeline, _SILVER_FNS[name])()
        return _run()

    @task
    def label_8ks() -> dict:
        from ml import items
        return items.run()

    @task
    def freshness_gate() -> dict:
        """Learned per-source staleness thresholds; hard-fails the run when a
        source has genuinely stopped (not merely a quiet weekend)."""
        from ingestion import freshness
        r = freshness.check()
        if not r["passed"]:
            stale = [s["source"] for s in r["results"] if s.get("stale")]
            raise RuntimeError(f"stale sources: {stale}")
        return {"n_sources": len(r["results"])}

    @task
    def refresh_dbt_manifest() -> str:
        """Keep the Cosmos render source current. The task group's shape is read
        from dbt/target/manifest.json at DAG-parse time, so a model added since
        the last `compose up` would otherwise not appear until a restart. Cheap
        (`dbt parse`, no warehouse work) and it runs before the build it feeds."""
        import subprocess

        from atlas_common import ATLAS_ROOT
        subprocess.run(
            ["dbt", "parse", "--profiles-dir", f"{ATLAS_ROOT}/dbt", "--target", "dev"],
            cwd=f"{ATLAS_ROOT}/dbt", check=True,
        )
        return f"{ATLAS_ROOT}/dbt/target/manifest.json"

    @task
    def detect_events() -> dict:
        from events import detect
        results = detect.run()
        if any(v < 0 for v in results.values()):
            raise RuntimeError(f"detector failure: {results}")
        return results

    @task
    def score_8ks() -> int:
        from ml import model
        return model.score_new()

    @task
    def reaction_predict() -> int:
        from ml import reaction
        return reaction.predict()

    @task
    def derive_alerts() -> int:
        from events import rules
        return rules.run()

    uni = sync_universe()
    polled = poll_edgar()
    docs = fetch_documents()
    prices = ingest_prices()
    silvers = [_silver(n) for n in ("form4", "form13f", "exhibit21", "sched13dg")]
    labels = label_8ks()
    gate = freshness_gate()
    manifest = refresh_dbt_manifest()
    dbt = dbt_task_group()
    detected = detect_events()
    scored = score_8ks()
    predicted = reaction_predict()
    alerts = derive_alerts()

    uni >> polled >> docs
    docs >> silvers
    docs >> labels
    [*silvers, labels, prices, polled] >> gate >> manifest >> dbt >> detected
    detected >> [scored, predicted] >> alerts


daily_pipeline()
