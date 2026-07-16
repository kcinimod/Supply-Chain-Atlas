"""Weekly retrain + full-refresh — Sundays 10:00 SGT (and manual trigger).

    refresh 8-K labels ─> train stage-1 ─> train reaction model ─> score backlog
                                     └─> dbt --full-refresh (rebuild incremental
                                         facts, catching restated history the
                                         daily lookback windows can't see)

Training NEVER runs on the daily tick (v1 retrained the model every day — a
stated anti-pattern). Promotion is the registry's challenger policy: the new
version becomes @champion only if its test metric is at least as good, so a
bad retrain can't silently take over serving; rollback = move the alias back.
"""
from __future__ import annotations

from airflow.decorators import dag, task

from atlas_common import DEFAULT_ARGS, START, dbt_task_group


@dag(
    dag_id="weekly_retrain",
    schedule="0 10 * * 0",
    start_date=START,
    catchup=False,
    default_args=DEFAULT_ARGS,
    max_active_runs=1,
    tags=["atlas", "mlops"],
)
def weekly_retrain():

    @task
    def refresh_labels() -> dict:
        from ml import items
        return items.run()

    @task
    def train_stage1() -> dict:
        from ml import model
        r = model.train()
        return {"version": r["version"], "roc_auc": r["roc_auc"],
                "promoted": r["promoted"]}

    @task
    def train_reaction() -> dict:
        from ml import reaction
        r = reaction.train()
        return {"version": r["version"], "roc_auc": r["roc_auc"],
                "hit_rate": r["hit_rate"], "promoted": r["promoted"]}

    @task
    def score_backlog() -> int:
        from ml import model, reaction
        n = model.score_new(limit=2000)
        n += reaction.predict()
        return n

    dbt_full = dbt_task_group(group_id="dbt_full_refresh", full_refresh=True)

    labels = refresh_labels()
    s1 = train_stage1()
    s2 = train_reaction()
    labels >> s1 >> s2 >> score_backlog()
    labels >> dbt_full


weekly_retrain()
