"""Postgres IO for the market-reaction model (stage 2).

The feature/label SQL lives here so the model code stays sklearn-only.
Everything is set-based: one query builds the whole training frame, one query
backfills every closeable actual — no per-event loops over the price fact.
"""
from __future__ import annotations

import datetime as dt
import json

from db.pool import connection

# Feature columns produced by event_features() — the single source of truth
# shared by training and prediction (no training-serving skew by construction).
FEATURE_COLUMNS = [
    "event_type", "module", "side", "horizon",          # categorical
    "clf_proba", "pre_car_5d", "pre_car_21d",           # numeric
    "pre_vol_21d", "volume_ratio",
]

_EVENT_FEATURE_SQL = """
SELECT e.event_id, e.event_type, e.ticker, e.event_date,
       (e.details->>'clf_proba')::float          AS clf_proba,
       um.module, um.side,
       pre.pre_car_5d, pre.pre_car_21d, pre.pre_vol_21d, pre.volume_ratio
FROM filing_event e
JOIN universe_member um ON um.ticker = e.ticker AND um.active
LEFT JOIN LATERAL (
    SELECT sum(t.abnormal_return) FILTER (WHERE t.rn <= 5)   AS pre_car_5d,
           sum(t.abnormal_return)                            AS pre_car_21d,
           stddev_samp(t.daily_return)                       AS pre_vol_21d,
           max(t.volume_ratio_20d) FILTER (WHERE t.rn = 1)   AS volume_ratio
    FROM (
        SELECT p.abnormal_return, p.daily_return, p.volume_ratio_20d,
               row_number() OVER (ORDER BY p.trade_date DESC) AS rn
        FROM analytics.fact_price_daily p
        WHERE p.ticker = e.ticker AND p.trade_date <= e.event_date
        ORDER BY p.trade_date DESC
        LIMIT 21
    ) t
) pre ON TRUE
WHERE e.ticker IS NOT NULL
"""


def event_features(event_ids: list[int] | None = None) -> list[dict]:
    """One row per tradeable event with prediction-time features."""
    sql, params = _EVENT_FEATURE_SQL, ()
    if event_ids is not None:
        sql += " AND e.event_id = ANY(%s)"
        params = (event_ids,)
    cols = ("event_id", "event_type", "ticker", "event_date", "clf_proba",
            "module", "side", "pre_car_5d", "pre_car_21d", "pre_vol_21d",
            "volume_ratio")
    with connection(readonly=True) as conn:
        return [dict(zip(cols, r)) for r in conn.execute(sql, params).fetchall()]


def event_labels(horizons: list[tuple[str, int]]) -> dict[int, dict[str, float]]:
    """event_id -> {horizon: CAR} for every event whose window has CLOSED
    (exactly N bars exist after event_date). One set-based pass."""
    max_days = max(d for _h, d in horizons)
    car_cols = ", ".join(
        f"sum(abnormal_return) FILTER (WHERE rn <= {d}) AS car_{h}, "
        f"count(*) FILTER (WHERE rn <= {d}) AS n_{h}"
        for h, d in horizons
    )
    sql = f"""
        WITH bars AS (
            SELECT e.event_id, p.abnormal_return,
                   row_number() OVER (PARTITION BY e.event_id
                                      ORDER BY p.trade_date) AS rn
            FROM filing_event e
            JOIN analytics.fact_price_daily p
              ON p.ticker = e.ticker AND p.trade_date > e.event_date
            WHERE e.ticker IS NOT NULL AND p.abnormal_return IS NOT NULL
        )
        SELECT event_id, {car_cols}
        FROM (SELECT * FROM bars WHERE rn <= {max_days}) b
        GROUP BY event_id
    """
    out: dict[int, dict[str, float]] = {}
    with connection(readonly=True) as conn:
        for row in conn.execute(sql).fetchall():
            event_id, rest = row[0], row[1:]
            labels: dict[str, float] = {}
            for i, (h, d) in enumerate(horizons):
                car, n = rest[2 * i], rest[2 * i + 1]
                if n == d and car is not None:  # window fully closed
                    labels[h] = float(car)
            if labels:
                out[event_id] = labels
    return out


def events_needing_prediction(model_version: str, horizons: list[str]) -> list[int]:
    """Events missing a prediction row for this model version on any horizon."""
    with connection(readonly=True) as conn:
        return [r[0] for r in conn.execute(
            """
            SELECT DISTINCT e.event_id
            FROM filing_event e
            WHERE e.ticker IS NOT NULL
              AND (SELECT count(*) FROM reaction_prediction rp
                   WHERE rp.event_id = e.event_id
                     AND rp.model_version = %s
                     AND rp.horizon = ANY(%s)) < %s
            """, (model_version, horizons, len(horizons))).fetchall()]


def insert_predictions(rows: list[dict]) -> int:
    """rows: {event_id, horizon, model_version, ticker, event_date, proba_up,
    predicted_up, features}. Prediction rows are immutable once written —
    conflicts are skipped, never overwritten (the log is the audit trail)."""
    if not rows:
        return 0
    new = 0
    with connection() as conn:
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "INSERT INTO reaction_prediction "
                    "  (event_id, horizon, model_version, ticker, event_date, "
                    "   proba_up, predicted_up, features) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (event_id, horizon, model_version) DO NOTHING",
                    (r["event_id"], r["horizon"], r["model_version"], r["ticker"],
                     r["event_date"], r["proba_up"], r["predicted_up"],
                     json.dumps(r.get("features", {}), default=str)),
                )
                new += cur.rowcount
    return new


def backfill_actuals(horizons: list[tuple[str, int]]) -> int:
    """Fill actual_car/actual_up on every open prediction whose trading-day
    window has closed. Set-based: one UPDATE per horizon."""
    filled = 0
    with connection() as conn:
        for horizon, days in horizons:
            cur = conn.execute(
                f"""
                UPDATE reaction_prediction rp
                SET actual_car = w.car,
                    actual_up = (w.car > 0),
                    window_closed_on = w.window_end,
                    evaluated_at = now()
                FROM (
                    SELECT b.event_id, b.ticker, sum(b.abnormal_return) AS car,
                           max(b.trade_date) AS window_end, count(*) AS n
                    FROM (
                        SELECT p0.event_id, p0.ticker, p.abnormal_return, p.trade_date,
                               row_number() OVER (PARTITION BY p0.event_id
                                                  ORDER BY p.trade_date) AS rn
                        FROM (SELECT DISTINCT event_id, ticker, event_date
                              FROM reaction_prediction
                              WHERE horizon = %s AND actual_car IS NULL) p0
                        JOIN analytics.fact_price_daily p
                          ON p.ticker = p0.ticker AND p.trade_date > p0.event_date
                        WHERE p.abnormal_return IS NOT NULL
                    ) b
                    WHERE b.rn <= %s
                    GROUP BY b.event_id, b.ticker
                    HAVING count(*) = %s
                ) w
                WHERE rp.event_id = w.event_id AND rp.horizon = %s
                  AND rp.actual_car IS NULL
                """, (horizon, days, days, horizon))
            filled += cur.rowcount
    return filled


def eval_frame(model_version: str, window_days: int = 180) -> list[dict]:
    """Evaluated predictions (actuals present) for the metric computation."""
    cols = ("event_id", "horizon", "proba_up", "predicted_up", "actual_up",
            "actual_car")
    with connection(readonly=True) as conn:
        return [dict(zip(cols, r)) for r in conn.execute(
            """
            SELECT event_id, horizon, proba_up, predicted_up, actual_up, actual_car
            FROM reaction_prediction
            WHERE model_version = %s AND actual_car IS NOT NULL
              AND event_date >= current_date - %s
            """, (model_version, window_days)).fetchall()]


def write_eval_metrics(model_version: str, horizon: str, metrics: dict[str, float]) -> None:
    with connection() as conn:
        with conn.cursor() as cur:
            for metric, value in metrics.items():
                cur.execute(
                    "INSERT INTO reaction_eval_metric "
                    "  (model_version, horizon, metric, value) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (model_version, horizon, metric, value))


def save_drift_baseline(model_name: str, model_version: str,
                        fractions: list[float], n: int) -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO ml_drift_baseline (model_name, model_version, bin_fractions, n_scores) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (model_name, model_version) DO UPDATE SET "
            "  bin_fractions = EXCLUDED.bin_fractions, n_scores = EXCLUDED.n_scores, "
            "  created_at = now()",
            (model_name, model_version, fractions, n))


def load_drift_baseline(model_name: str, model_version: str) -> list[float] | None:
    with connection(readonly=True) as conn:
        row = conn.execute(
            "SELECT bin_fractions FROM ml_drift_baseline "
            "WHERE model_name = %s AND model_version = %s",
            (model_name, model_version)).fetchone()
    return [float(v) for v in row[0]] if row else None
