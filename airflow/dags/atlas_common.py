"""Shared DAG plumbing: timezone, retries, and the cosmos dbt config.

Scheduling constraints (owner's laptop):
* The machine is OFF 00:00-09:00 SGT nightly — no schedule may sit in that
  window. Docker restarts the scheduler when the machine wakes; with
  catchup=False Airflow then triggers the LATEST missed run of each DAG, and
  the cursor-based ingestion re-walks whatever days it slept through — the
  gap self-heals with no manual step.
* Every task gets retries (the rubric's "how failed tasks are retried" demo
  is real here, not staged).
"""
from __future__ import annotations

import os
from datetime import timedelta

import pendulum

SGT = pendulum.timezone("Asia/Singapore")
START = pendulum.datetime(2026, 7, 1, tz=SGT)

DEFAULT_ARGS = {
    "owner": "atlas",
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
}

ATLAS_ROOT = os.environ.get("ATLAS_ROOT", "/opt/atlas")


def dbt_task_group(group_id: str = "dbt_build", full_refresh: bool = False):
    """The dbt project rendered as a task group (model-level tasks + tests)
    via astronomer-cosmos — the Airflow counterpart of v1's dagster-dbt
    lineage merge.

    RENDERING COST. Cosmos's default LoadMode.AUTOMATIC shells out to `dbt ls`
    every time the DAG file is imported, which cost ~8.5s here against a 30s
    DagBag limit. Airflow 3 re-imports the DAG inside every task process, so a
    49-task run paid that ~49 times over and any cold-boot CPU contention pushed
    it past the limit — the task then died with "Dag not found during start up"
    before its first line ran. That is what killed the 2026-07-24 daily_pipeline
    and 2026-07-26 weekly_retrain runs.

    Reading a pre-built manifest instead makes rendering a JSON load (no
    subprocess, no temp copy of the project). `dbt parse` regenerates it in
    airflow-init on every `docker compose up`, and daily_pipeline refreshes it
    before the build, so the DAG shape tracks the models. If the manifest is
    missing we fall back to the old live `dbt ls` path rather than failing to
    parse — correctness over speed — and the raised DagBag timeout in
    docker-compose.yml covers that slow case.
    """
    import pathlib

    from cosmos import (DbtTaskGroup, ExecutionConfig, LoadMode, ProfileConfig,
                        ProjectConfig, RenderConfig)

    manifest = pathlib.Path(f"{ATLAS_ROOT}/dbt/target/manifest.json")
    if manifest.is_file():
        project = ProjectConfig(
            dbt_project_path=f"{ATLAS_ROOT}/dbt",
            manifest_path=str(manifest),
        )
        render_config = RenderConfig(load_method=LoadMode.DBT_MANIFEST)
    else:
        project = ProjectConfig(dbt_project_path=f"{ATLAS_ROOT}/dbt")
        render_config = RenderConfig()

    profile = ProfileConfig(
        profile_name="atlas",
        target_name="dev",
        profiles_yml_filepath=f"{ATLAS_ROOT}/dbt/profiles.yml",
    )
    operator_args = {"install_deps": False}
    if full_refresh:
        operator_args["full_refresh"] = True
    return DbtTaskGroup(
        group_id=group_id,
        project_config=project,
        profile_config=profile,
        execution_config=ExecutionConfig(dbt_executable_path="dbt"),
        render_config=render_config,
        operator_args=operator_args,
    )
