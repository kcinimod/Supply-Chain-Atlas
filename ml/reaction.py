"""Stage 2: the market-reaction model.

Predicts, for every detected filing event, whether the affected company's
cumulative ABNORMAL return (vs the benchmark) over each horizon will be
positive. Horizons are data (reaction_horizon table): 3d / 1w / 1m / 3m today,
extendable by INSERT.

Design choices
--------------
* ONE registered model; the horizon is a categorical FEATURE. New horizons
  need no new registry entries, and the champion alias stays a single pointer.
* Features come from `reaction_store.event_features` — the same query at train
  and predict time, so training-serving skew is impossible by construction.
* Time-ordered split on event_date (test = the future), like stage 1.
* Honesty: short-horizon reaction prediction is hard; near-coin-flip test AUC
  is an acceptable *finding*. What is graded — and what the SaaS needs — is
  the loop: predict at event time, backfill actuals as bars arrive, evaluate
  daily at 12:00 SGT, alert on degradation.
"""
from __future__ import annotations

import datetime as dt
import logging

import mlflow
import mlflow.sklearn
import numpy as np
from mlflow.tracking import MlflowClient
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from events import store as event_store
from ml import config, registry, reaction_store

log = logging.getLogger(__name__)

_CATEGORICAL = ["event_type", "module", "side", "horizon"]
_NUMERIC = ["clf_proba", "pre_car_5d", "pre_car_21d", "pre_vol_21d", "volume_ratio"]


def _pipeline() -> Pipeline:
    prep = ColumnTransformer([
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), _CATEGORICAL),
        ("num", SimpleImputer(strategy="median"), _NUMERIC),
    ])
    return Pipeline([
        ("prep", prep),
        ("clf", HistGradientBoostingClassifier(
            max_depth=4, learning_rate=0.08, max_iter=250,
            random_state=config.RANDOM_STATE)),
    ])


def _frame(rows: list[dict]):
    """dict rows -> pandas DataFrame with exactly the feature columns."""
    import pandas as pd
    df = pd.DataFrame(rows)
    for c in _CATEGORICAL:
        df[c] = df[c].astype(str)
    for c in _NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _training_rows() -> list[dict]:
    """(event x horizon) rows for every closed-window labeled pair."""
    horizons = event_store.active_horizons()
    features = {f["event_id"]: f for f in reaction_store.event_features()}
    labels = reaction_store.event_labels(horizons)

    rows: list[dict] = []
    for event_id, per_h in labels.items():
        f = features.get(event_id)
        if f is None:
            continue
        for horizon, car in per_h.items():
            rows.append({**f, "horizon": horizon, "car": car, "y": int(car > 0)})
    rows.sort(key=lambda r: (r["event_date"], r["event_id"], r["horizon"]))
    return rows


# --- train -------------------------------------------------------------------
def train() -> dict:
    mlflow.set_tracking_uri(config.TRACKING_URI)
    mlflow.set_experiment(config.REACTION_EXPERIMENT)

    rows = _training_rows()
    if len(rows) < 100:
        raise RuntimeError(
            f"only {len(rows)} labeled (event x horizon) rows — ingest more "
            "events/prices before training the reaction model")

    df = _frame(rows)
    y = df["y"].to_numpy()
    # time-ordered split on event_date: the holdout is the future
    dates = sorted(df["event_date"].unique())
    cut_date = dates[max(1, int(len(dates) * (1 - config.TEST_FRACTION))) - 1]
    train_mask = (df["event_date"] <= cut_date).to_numpy()
    Xtr, Xte = df[train_mask], df[~train_mask]
    ytr, yte = y[train_mask], y[~train_mask]

    with mlflow.start_run() as run:
        pipe = _pipeline()
        pipe.fit(Xtr[reaction_store.FEATURE_COLUMNS], ytr)
        proba = pipe.predict_proba(Xte[reaction_store.FEATURE_COLUMNS])[:, 1]
        pred = (proba >= 0.5).astype(int)

        metrics = {
            "roc_auc": roc_auc_score(yte, proba) if len(set(yte)) > 1 else float("nan"),
            "hit_rate": accuracy_score(yte, pred),
            "brier": brier_score_loss(yte, proba),
            "n_train": float(train_mask.sum()),
            "n_test": float((~train_mask).sum()),
            "base_rate_up": float(y.mean()),
        }
        # per-horizon test hit-rate — the honest breakdown
        for h in Xte["horizon"].unique():
            m = (Xte["horizon"] == h).to_numpy()
            if m.sum() >= 10:
                metrics[f"hit_rate_{h}"] = accuracy_score(yte[m], pred[m])

        mlflow.log_params({
            "model": "hgb", "max_depth": 4, "learning_rate": 0.08,
            "features": ",".join(reaction_store.FEATURE_COLUMNS),
            "cut_date": str(cut_date), "test_fraction": config.TEST_FRACTION,
        })
        mlflow.log_metrics({k: v for k, v in metrics.items() if v == v})
        # MLflow 3 serializes sklearn via skops, which requires explicitly
        # trusting the numpy dtypes HistGradientBoosting embeds.
        mlflow.sklearn.log_model(pipe, name="model",
                                 registered_model_name=config.REACTION_MODEL_NAME,
                                 skops_trusted_types=["numpy.dtype"])
        run_id = run.info.run_id

    version = registry.latest_version(config.REACTION_MODEL_NAME)
    # Freeze the drift baseline: train-score distribution at train time.
    train_proba = pipe.predict_proba(Xtr[reaction_store.FEATURE_COLUMNS])[:, 1]
    fractions = np.histogram(train_proba, np.linspace(0, 1, 11))[0] / max(len(train_proba), 1)
    reaction_store.save_drift_baseline(config.REACTION_MODEL_NAME, version,
                                       [float(f) for f in fractions], len(train_proba))
    promoted = registry.maybe_promote(config.REACTION_MODEL_NAME, version,
                                      metric="roc_auc", value=metrics["roc_auc"])
    log.info("reaction train: v%s roc_auc=%.3f hit=%.3f brier=%.3f (n=%d/%d) champion=%s",
             version, metrics["roc_auc"], metrics["hit_rate"], metrics["brier"],
             metrics["n_train"], metrics["n_test"], promoted)
    return {"version": version, "run_id": run_id, "promoted": promoted, **metrics}


# --- predict -------------------------------------------------------------------
def predict() -> int:
    """Score every event that lacks a prediction from the current champion,
    across all active horizons. Runs inside the daily pipeline right after
    event detection — predictions are on record BEFORE outcomes exist."""
    mlflow.set_tracking_uri(config.TRACKING_URI)
    model, version = registry.load_champion(config.REACTION_MODEL_NAME)
    horizons = [h for h, _d in event_store.active_horizons()]

    todo = reaction_store.events_needing_prediction(version, horizons)
    if not todo:
        log.info("reaction predict: nothing to score (champion v%s)", version)
        return 0

    features = reaction_store.event_features(todo)
    rows: list[dict] = []
    for f in features:
        for h in horizons:
            rows.append({**f, "horizon": h})
    df = _frame(rows)
    proba = model.predict_proba(df[reaction_store.FEATURE_COLUMNS])[:, 1]

    out = [{
        "event_id": r["event_id"], "horizon": r["horizon"], "model_version": version,
        "ticker": r["ticker"], "event_date": r["event_date"],
        "proba_up": float(p), "predicted_up": bool(p >= 0.5),
        "features": {k: r.get(k) for k in reaction_store.FEATURE_COLUMNS},
    } for r, p in zip(rows, proba)]
    n = reaction_store.insert_predictions(out)
    log.info("reaction predict: %d event(s) -> %d new prediction rows (v%s)",
             len(todo), n, version)
    return n


# --- evaluate (the 12:00 SGT loop) ---------------------------------------------
def evaluate() -> dict:
    """Backfill actuals for closed windows, compute per-horizon metrics over
    the trailing 180 days, append the metric time series, log to MLflow, and
    raise a degradation alert when the champion underperforms."""
    mlflow.set_tracking_uri(config.TRACKING_URI)
    mlflow.set_experiment(config.REACTION_EXPERIMENT)
    _model, version = registry.load_champion(config.REACTION_MODEL_NAME)
    horizons = event_store.active_horizons()

    filled = reaction_store.backfill_actuals(horizons)
    frame = reaction_store.eval_frame(version)

    summary: dict[str, dict] = {}
    with mlflow.start_run(run_name=f"eval_{dt.date.today().isoformat()}"):
        mlflow.set_tag("kind", "scheduled_eval")
        for horizon, _days in horizons:
            rows = [r for r in frame if r["horizon"] == horizon]
            if len(rows) < 5:
                continue
            y = np.array([int(r["actual_up"]) for r in rows])
            proba = np.array([float(r["proba_up"]) for r in rows])
            pred = (proba >= 0.5).astype(int)
            metrics = {
                "hit_rate": float(accuracy_score(y, pred)),
                "brier": float(brier_score_loss(y, proba)),
                "n": float(len(rows)),
            }
            reaction_store.write_eval_metrics(version, horizon, metrics)
            mlflow.log_metrics({f"{horizon}_{k}": v for k, v in metrics.items()})
            summary[horizon] = metrics

            if (metrics["n"] >= config.REACTION_MIN_N_ALERT
                    and metrics["hit_rate"] < config.REACTION_HIT_RATE_ALERT):
                from events import rules
                rules.system_alert(
                    rule="reaction_model_degraded", severity="watch",
                    title=f"Reaction model under-performing at {horizon} "
                          f"(hit rate {metrics['hit_rate']:.0%}, n={int(metrics['n'])})",
                    body=f"Champion v{version} trailing-180d hit rate fell below "
                         f"{config.REACTION_HIT_RATE_ALERT:.0%}. Consider retraining "
                         f"or reviewing feature drift.",
                    dedup_key=f"reaction_degraded:{version}:{horizon}:{dt.date.today()}",
                )

    log.info("reaction eval: v%s backfilled %d actuals; %s", version, filled,
             {h: f"hit={m['hit_rate']:.2f} n={int(m['n'])}" for h, m in summary.items()})
    return {"version": version, "backfilled": filled, "horizons": summary}
