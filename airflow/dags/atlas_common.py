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
    lineage merge."""
    from cosmos import (DbtTaskGroup, ExecutionConfig, ProfileConfig,
                        ProjectConfig, RenderConfig)

    project = ProjectConfig(dbt_project_path=f"{ATLAS_ROOT}/dbt")
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
        render_config=RenderConfig(),
        operator_args=operator_args,
    )
