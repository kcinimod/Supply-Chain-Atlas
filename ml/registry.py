"""MLflow model-registry helpers shared by both models.

v1 resolved "the model" as max(version) — fragile, and there was no promotion
concept. v2 serves through the @champion ALIAS: training registers a
challenger, `maybe_promote` moves the alias only when the challenger's test
metric is at least as good, and every consumer (predict, eval, drift, the
serving API) loads `models:/<name>@champion`. Rollback = move the alias back.
"""
from __future__ import annotations

import logging

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient

from ml import config

log = logging.getLogger(__name__)

_METRIC_TAG = "promotion_metric"        # tag on each version: "<metric>=<value>"


def _client() -> MlflowClient:
    return MlflowClient(tracking_uri=config.TRACKING_URI)


def latest_version(name: str) -> str:
    versions = _client().search_model_versions(f"name='{name}'")
    if not versions:
        raise RuntimeError(f"no registered '{name}' — train first")
    return max(versions, key=lambda v: int(v.version)).version


def champion_version(name: str) -> str | None:
    try:
        return _client().get_model_version_by_alias(name, config.CHAMPION_ALIAS).version
    except Exception:
        return None


def load_champion(name: str):
    """Load `models:/<name>@champion`; if no champion exists yet (first train),
    fall back to the latest version and crown it."""
    mlflow.set_tracking_uri(config.TRACKING_URI)
    version = champion_version(name)
    if version is None:
        version = latest_version(name)
        _client().set_registered_model_alias(name, config.CHAMPION_ALIAS, version)
        log.info("registry: no champion for %s — crowned v%s", name, version)
    model = mlflow.sklearn.load_model(f"models:/{name}@{config.CHAMPION_ALIAS}")
    return model, version


def maybe_promote(name: str, version: str, *, metric: str, value: float) -> bool:
    """Challenger-promotion policy: the new version becomes champion iff its
    test metric is >= the incumbent's recorded metric (or there is no
    incumbent). Records the metric as a version tag either way."""
    client = _client()
    client.set_model_version_tag(name, version, _METRIC_TAG, f"{metric}={value:.6f}")

    incumbent = champion_version(name)
    if incumbent is None or incumbent == version:
        client.set_registered_model_alias(name, config.CHAMPION_ALIAS, version)
        return True

    try:
        tag = client.get_model_version(name, incumbent).tags.get(_METRIC_TAG, "")
        incumbent_value = float(tag.split("=", 1)[1]) if "=" in tag else float("-inf")
    except Exception:
        incumbent_value = float("-inf")

    if value != value:  # NaN metric (degenerate test slice) never promotes
        log.info("registry: %s v%s metric is NaN — champion stays v%s",
                 name, version, incumbent)
        return False
    if value >= incumbent_value:
        client.set_registered_model_alias(name, config.CHAMPION_ALIAS, version)
        log.info("registry: %s champion v%s -> v%s (%s %.4f >= %.4f)",
                 name, incumbent, version, metric, value, incumbent_value)
        return True
    log.info("registry: %s keeps champion v%s (%s %.4f < %.4f)",
             name, incumbent, metric, value, incumbent_value)
    return False
