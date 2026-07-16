"""Train / evaluate / drift-check the 8-K substance classifier.

Baseline model: TF-IDF over the 8-K body + logistic regression. It is a genuine
supervised classifier (real precision/recall/F1/ROC-AUC), trained on a
time-ordered split so 'test' is the *future* relative to 'train' -- the honest
setup for a filing stream. Everything is logged and versioned in MLflow; the
registered model is what eval and drift load, exactly as a serving path would.
"""
from __future__ import annotations

import logging

import mlflow
import mlflow.sklearn
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, precision_score,
                             recall_score, roc_auc_score)
from sklearn.pipeline import Pipeline

from ml import config, reaction_store, registry, store, text

log = logging.getLogger(__name__)


# --- data ------------------------------------------------------------------
def _load_xy() -> tuple[list[str], np.ndarray, list[str]]:
    """Returns (texts, labels, accessions) ordered by filing_date."""
    rows = store.labeled_with_docs()
    if not rows:
        raise RuntimeError("no labelled 8-Ks with docs -- run `ml items` and fetch-docs first")
    texts, labels, accs = [], [], []
    for acc, doc_path, is_material, _date in rows:
        try:
            texts.append(text.extract_text(doc_path))
        except Exception as exc:      # a missing/corrupt doc shouldn't sink the run
            log.warning("skip %s: %s", acc, exc)
            continue
        labels.append(int(bool(is_material)))
        accs.append(acc)
    return texts, np.array(labels), accs


def _split(n: int) -> int:
    """Index of the time-ordered train/test boundary (tail = holdout)."""
    return max(1, int(n * (1 - config.TEST_FRACTION)))


def _pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(sublinear_tf=True, min_df=3, max_df=0.9,
                                  ngram_range=(1, 2), stop_words="english",
                                  max_features=50_000)),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced",
                                   C=4.0, random_state=config.RANDOM_STATE)),
    ])


def _configure_mlflow() -> None:
    mlflow.set_tracking_uri(config.TRACKING_URI)
    mlflow.set_experiment(config.EXPERIMENT)


def _load_registered():
    """v2: serving resolves through the @champion alias (see ml/registry.py)."""
    return registry.load_champion(config.MODEL_NAME)


# --- train -----------------------------------------------------------------
def train() -> dict:
    _configure_mlflow()
    texts, y, accs = _load_xy()
    cut = _split(len(texts))
    Xtr, Xte, ytr, yte = texts[:cut], texts[cut:], y[:cut], y[cut:]

    with mlflow.start_run() as run:
        pipe = _pipeline()
        pipe.fit(Xtr, ytr)
        proba = pipe.predict_proba(Xte)[:, 1]
        pred = (proba >= 0.5).astype(int)

        metrics = {
            "roc_auc": roc_auc_score(yte, proba) if len(set(yte)) > 1 else float("nan"),
            "f1": f1_score(yte, pred, zero_division=0),
            "precision": precision_score(yte, pred, zero_division=0),
            "recall": recall_score(yte, pred, zero_division=0),
            "accuracy": accuracy_score(yte, pred),
            "n_train": float(len(Xtr)),
            "n_test": float(len(Xte)),
            "material_rate": float(y.mean()),
        }
        mlflow.log_params({"model": "tfidf+logreg", "C": 4.0,
                           "ngram_range": "1-2", "test_fraction": config.TEST_FRACTION,
                           "n_material_items": len(config.MATERIAL_ITEMS)})
        mlflow.log_metrics({k: v for k, v in metrics.items() if v == v})  # drop NaN
        mlflow.sklearn.log_model(pipe, name="model",
                                 registered_model_name=config.MODEL_NAME)
        run_id = run.info.run_id

    version = registry.latest_version(config.MODEL_NAME)
    store.write_predictions(version, list(zip(
        accs[cut:], (float(p) for p in proba),
        (bool(p) for p in pred), (bool(v) for v in yte))))
    store.write_metrics(version, "test", metrics)

    # Freeze the drift baseline at train time: the TRAIN-slice score histogram.
    # The drift check compares recent scores against this stored distribution —
    # never against a re-scored live corpus (v1's self-referential mistake).
    train_proba = pipe.predict_proba(Xtr)[:, 1]
    fractions = np.histogram(train_proba, np.linspace(0, 1, 11))[0] / max(len(train_proba), 1)
    reaction_store.save_drift_baseline(config.MODEL_NAME, version,
                                       [float(f) for f in fractions], len(train_proba))
    promoted = registry.maybe_promote(config.MODEL_NAME, version,
                                      metric="roc_auc", value=metrics["roc_auc"])
    log.info("train: model v%s  roc_auc=%.3f f1=%.3f (n_test=%d) champion=%s",
             version, metrics["roc_auc"], metrics["f1"], len(Xte), promoted)
    return {"version": version, "run_id": run_id, "promoted": promoted, **metrics}


# --- evaluate (append metric time-series over a recent slice) ---------------
def evaluate(recent_frac: float = 0.2) -> dict:
    """Re-scores the most-recent labeled slice with the champion. Honest name:
    the labels are Item-code-derived (known at labeling time), so this split is
    'recent' — a health check on fresh filings, not outcome-based truth. The
    outcome-based loop lives in ml/reaction.py."""
    _configure_mlflow()
    model, version = _load_registered()
    texts, y, accs = _load_xy()
    k = max(1, int(len(texts) * recent_frac))
    Xr, yr, ar = texts[-k:], y[-k:], accs[-k:]

    proba = model.predict_proba(Xr)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics = {
        "roc_auc": roc_auc_score(yr, proba) if len(set(yr)) > 1 else float("nan"),
        "f1": f1_score(yr, pred, zero_division=0),
        "precision": precision_score(yr, pred, zero_division=0),
        "recall": recall_score(yr, pred, zero_division=0),
        "n_eval": float(len(Xr)),
    }
    store.write_predictions(version, list(zip(
        ar, (float(p) for p in proba), (bool(p) for p in pred), (bool(v) for v in yr))))
    store.write_metrics(version, "recent", {k2: v for k2, v in metrics.items() if v == v})
    log.info("eval: model v%s over %d recent 8-Ks  roc_auc=%.3f f1=%.3f",
             version, len(Xr), metrics["roc_auc"], metrics["f1"])
    return {"version": version, **metrics}


# --- production scoring (stage-1 filter feeding the event layer) -------------
def score_new(limit: int = 500) -> int:
    """Score stored 8-K bodies the champion hasn't seen — the production
    inference path. The score lands in eightk_prediction, where the event
    detector attaches it to material_8k events (details.clf_proba)."""
    _configure_mlflow()
    model, version = _load_registered()
    rows = store.unscored_8ks(version, limit)
    if not rows:
        log.info("score: nothing new for champion v%s", version)
        return 0
    out = []
    for acc, doc_path, is_material in rows:
        try:
            proba = float(model.predict_proba([text.extract_text(doc_path)])[0, 1])
        except Exception as exc:
            log.warning("score: skip %s: %s", acc, exc)
            continue
        out.append((acc, proba, proba >= 0.5, is_material))
    n = store.write_predictions(version, out)
    log.info("score: %d new 8-K(s) scored by champion v%s", n, version)
    return n


# --- drift (asset-check): PSI vs the FROZEN train-time baseline --------------
def _psi_from_fractions(ref_fractions: np.ndarray, current: np.ndarray,
                        bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    ref = np.clip(ref_fractions, 1e-4, None)
    cur = np.clip(np.histogram(current, edges)[0] / max(len(current), 1), 1e-4, None)
    return float(np.sum((cur - ref) * np.log(cur / ref)))


def drift(recent_n: int | None = None) -> dict:
    """PSI between the champion's FROZEN train-time score distribution (stored
    at train in ml_drift_baseline) and its scores on a bounded recent slice.
    v1 compared a live corpus against its own tail — self-referential and
    unbounded; this is a true training-baseline-vs-now comparison."""
    _configure_mlflow()
    model, version = _load_registered()
    baseline = reaction_store.load_drift_baseline(config.MODEL_NAME, version)
    if baseline is None:
        raise RuntimeError(
            f"no frozen drift baseline for {config.MODEL_NAME} v{version} — retrain once")

    recent = store.recent_docs(recent_n or config.DRIFT_RECENT_N)
    cur_scores = []
    for _acc, doc_path in recent:
        try:
            cur_scores.append(float(model.predict_proba([text.extract_text(doc_path)])[0, 1]))
        except Exception:
            continue
    if not cur_scores:
        raise RuntimeError("no recent stored 8-K bodies to score for drift")

    psi = _psi_from_fractions(np.array(baseline), np.array(cur_scores))
    passed = psi < config.DRIFT_PSI_FAIL
    store.write_metrics(version, "drift", {"psi": psi,
                                           "n_cur": float(len(cur_scores))})
    log.info("drift: model v%s  PSI=%.3f vs frozen baseline (warn>%.2f fail>%.2f) -> %s",
             version, psi, config.DRIFT_PSI_WARN, config.DRIFT_PSI_FAIL,
             "PASS" if passed else "FAIL")
    return {"version": version, "psi": psi, "passed": passed,
            "warn": psi >= config.DRIFT_PSI_WARN}
