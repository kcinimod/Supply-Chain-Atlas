"""The 12:00 SGT evaluation loop — the rubric's predict-vs-actuals exemplar.

    backfill actuals + per-horizon metrics (reaction model)
        └─> stage-1 recent-slice health check
        └─> drift check vs the FROZEN train baseline (fails the task on PSI breach)
        └─> pipeline-health alerts (stale sources)

Runs three hours after the 09:30 ingest so today's bars and filings are in.
Degradation alerting happens inside reaction.evaluate() (hit-rate floor) and
in drift_check below — alerts land in the same `alert` table the dashboard
serves, so model health is user-visible, not just log-visible.
"""
from __future__ import annotations

import datetime as dt

from airflow.decorators import dag, task

from atlas_common import DEFAULT_ARGS, START


@dag(
    dag_id="daily_eval",
    schedule="0 12 * * *",
    start_date=START,
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["atlas", "mlops"],
)
def daily_eval():

    @task
    def reaction_eval() -> dict:
        """Backfill actual CARs for every closed horizon window, append the
        per-horizon metric time series, alert if the champion degrades."""
        from ml import reaction
        r = reaction.evaluate()
        return {"backfilled": r["backfilled"],
                "horizons": {h: m["hit_rate"] for h, m in r["horizons"].items()}}

    @task
    def stage1_eval() -> dict:
        from ml import model
        r = model.evaluate()
        return {k: v for k, v in r.items() if isinstance(v, (int, float, str))}

    @task
    def drift_check() -> dict:
        """PSI vs frozen baseline. WARN raises an alert; FAIL also fails the
        task (visible red in the DAG — a real gate, not v1's cosmetic WARN)."""
        from ml import model
        r = model.drift()
        if r["warn"] or not r["passed"]:
            from events import rules
            rules.system_alert(
                rule="clf_drift", severity="watch" if r["passed"] else "high",
                title=f"8-K classifier drift PSI={r['psi']:.2f} "
                      f"({'FAIL' if not r['passed'] else 'WARN'})",
                body="Score distribution has shifted vs the frozen training "
                     "baseline. Review recent filings mix; consider retraining.",
                dedup_key=f"clf_drift:{r['version']}:{dt.date.today()}",
            )
        if not r["passed"]:
            raise RuntimeError(f"drift PSI {r['psi']:.3f} breached fail threshold")
        return r

    @task
    def freshness_alerts() -> int:
        """Quiet-source alerting (does NOT fail this DAG — the ingest DAG owns
        the hard gate; here we only surface it to the dashboard)."""
        from ingestion import freshness
        from events import rules
        r = freshness.check()
        n = 0
        for s in r["results"]:
            if s.get("stale"):
                n += rules.system_alert(
                    rule="source_stale", severity="watch",
                    title=f"Source quiet: {s['source']} "
                          f"({s['days_since']}d, limit {s['threshold']}d)",
                    body="No filings from this source within its learned gap "
                         "threshold. Verify EDGAR poller and universe scope.",
                    dedup_key=f"source_stale:{s['source']}:{dt.date.today()}",
                )
        return n

    reaction_eval() >> stage1_eval() >> drift_check() >> freshness_alerts()


daily_eval()
