"""Phase 5 config: the materiality label rule + MLflow locations.

The label rule is a deliberate, defensible design choice (graded), not ground
truth -- an 8-K's Item codes classify the *nature* of the event, and we map the
high-impact items to 'material'. 7.01 (Reg FD) and 8.01 (Other Events) are the
honest grey zone: they are catch-alls that SOMETIMES carry a market-moving press
release, so we label them routine-by-default and let that be a stated limitation.
"""
from __future__ import annotations

from ingestion import config as _ing

# --- the label oracle ------------------------------------------------------
# High-signal 8-K items -> material. Anything else (5.03/5.05/5.07/5.08 governance
# housekeeping, 7.01 Reg FD, 8.01 Other, 9.01 exhibits-only, 3.02/3.03, ...) -> routine.
MATERIAL_ITEMS: frozenset[str] = frozenset({
    "1.01",  # entry into a material definitive agreement
    "1.02",  # termination of a material agreement
    "1.03",  # bankruptcy or receivership
    "1.05",  # material cybersecurity incident
    "2.01",  # completion of acquisition / disposition of assets
    "2.02",  # results of operations (earnings)
    "2.03",  # creation of a direct financial obligation
    "2.04",  # triggering events accelerating an obligation
    "2.05",  # costs associated with exit / disposal
    "2.06",  # material impairments
    "3.01",  # notice of delisting / failure to satisfy listing rule
    "4.01",  # change in registrant's certifying accountant
    "4.02",  # non-reliance on previously issued financials (restatement)
    "5.01",  # changes in control of registrant
    "5.02",  # departure / election of directors or officers
})

TARGET_8K_FORMS = ("8-K", "8-K/A")

# --- MLflow ------------------------------------------------------------------
# v2: a real tracking SERVER (Docker Compose `mlflow`, Postgres-backed registry)
# via MLFLOW_TRACKING_URI; the sqlite file remains the offline fallback so the
# CLI still works with the stack down.
import os as _os

TRACKING_URI = _os.environ.get(
    "MLFLOW_TRACKING_URI",
    "sqlite:///" + (_ing.REPO_ROOT / "mlflow.db").as_posix(),
)
EXPERIMENT = "eightk_substance"
MODEL_NAME = "eightk_substance_clf"

# Stage-2: the market-reaction model (one registered model, horizon is a feature).
REACTION_EXPERIMENT = "market_reaction"
REACTION_MODEL_NAME = "reaction_clf"

# Registry alias serving loads — promotion moves this pointer, never the code.
CHAMPION_ALIAS = "champion"

# --- modelling knobs -------------------------------------------------------
RANDOM_STATE = 42
TEST_FRACTION = 0.2          # time-ordered holdout (most-recent 20%)
DRIFT_PSI_WARN = 0.2         # 0.1-0.2 = moderate shift, >0.2 = significant
DRIFT_PSI_FAIL = 0.25
DRIFT_RECENT_N = 400         # bounded recent slice scored by the drift check

# Reaction-model degradation alerting (12:00 SGT eval DAG)
REACTION_MIN_N_ALERT = 30    # don't alert on tiny samples
REACTION_HIT_RATE_ALERT = 0.45


def item_codes_are_material(codes: list[str]) -> bool | None:
    """None when no items are listed (unknown -> excluded from training)."""
    if not codes:
        return None
    return any(c in MATERIAL_ITEMS for c in codes)
