"""Per-source freshness / gap detection -- the standing guardrail for the
Phase-1 lesson (a renamed form type silently stopped arriving for months).

Two design choices make this robust rather than noisy:

1. LOGICAL SOURCES, not raw form strings. SEC's 2024-12-18 structured-data
   mandate renamed 'SC 13G' -> 'SCHEDULE 13G' (etc.). A naive per-string check
   would scream that 'SC 13G' has been silent for ~600 days -- a FALSE alarm, it
   was renamed. We group the old and new spellings into one source and take the
   most-recent across the group, so the rename is invisible but a true stoppage
   of the *current* spelling still trips the alert.

2. DATA-DRIVEN thresholds. Every source has its own cadence (Form 4 ~daily, 13F
   quarterly, 10-K annual). Rather than hand-tune a number per source, we learn
   each source's normal 'longest quiet stretch' from its own history (a high
   quantile of inter-filing gaps) and alert when the current silence exceeds it.
   This is also what catches the 'computer was off for weeks' case: when nothing
   has been ingested since the last run, the active sources go stale together.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from db.pool import connection

log = logging.getLogger(__name__)

# Old + new spellings collapsed into one logical source (the rename lesson in code).
LOGICAL_SOURCES: dict[str, list[str]] = {
    "Form 3 (new insider)":      ["3", "3/A"],
    "Form 4 (insider txn)":      ["4", "4/A"],
    "Form 5 (annual insider)":   ["5", "5/A"],
    "Form 8-K (material event)": ["8-K", "8-K/A"],
    "Form 10-K (annual report)": ["10-K", "10-K/A"],
    "Form 13F (holdings)":       ["13F-HR", "13F-HR/A"],
    "Schedule 13D (activist)":   ["SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"],
    "Schedule 13G (passive)":    ["SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"],
}
_FORM_TO_SOURCE = {f: src for src, forms in LOGICAL_SOURCES.items() for f in forms}

# Tuning: threshold = max(FLOOR_DAYS, quantile(gaps) * FACTOR). Sources with too
# little history to learn a cadence fall back to DEFAULT_THRESHOLD.
WINDOW_DAYS = 730
QUANTILE = 0.99
FACTOR = 2.0
FLOOR_DAYS = 14          # tolerate weekends/holidays even for daily sources
MIN_GAPS = 5
DEFAULT_THRESHOLD = 200


def _quantile(sorted_days: list[int], q: float) -> int:
    """Discrete quantile (like SQL percentile_disc) -- no numpy dependency here."""
    if not sorted_days:
        return 0
    idx = min(len(sorted_days) - 1, math.ceil(q * (len(sorted_days) - 1)))
    return sorted_days[idx]


def check(as_of: dt.date | None = None) -> dict:
    """Return per-source freshness vs a learned threshold. `as_of` lets a check
    run 'as of' any date (testing / historical demonstration)."""
    as_of = as_of or dt.date.today()
    floor = as_of - dt.timedelta(days=WINDOW_DAYS)

    with connection(readonly=True) as conn:
        rows = conn.execute(
            "SELECT DISTINCT form_type, filing_date FROM raw_filing "
            "WHERE filing_date BETWEEN %s AND %s ORDER BY form_type, filing_date",
            (floor, as_of),
        ).fetchall()

    # Collapse to one sorted date list per logical source.
    by_source: dict[str, list[dt.date]] = {src: [] for src in LOGICAL_SOURCES}
    for form, filing_date in rows:
        src = _FORM_TO_SOURCE.get(form)
        if src:
            by_source[src].append(filing_date)

    results = []
    for src, dates in by_source.items():
        if not dates:
            results.append({"source": src, "last_filing": None, "days_since": None,
                            "threshold": None, "n_gaps": 0, "stale": False, "no_data": True})
            continue
        dates = sorted(set(dates))
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
        if len(gaps) >= MIN_GAPS:
            threshold = max(FLOOR_DAYS, math.ceil(_quantile(gaps, QUANTILE) * FACTOR))
        else:
            threshold = DEFAULT_THRESHOLD
        days_since = (as_of - dates[-1]).days
        results.append({
            "source": src, "last_filing": dates[-1], "days_since": days_since,
            "threshold": threshold, "n_gaps": len(gaps),
            "stale": days_since > threshold, "no_data": False,
        })

    results.sort(key=lambda r: (r["days_since"] or 0) - (r["threshold"] or 0), reverse=True)
    stale = [r for r in results if r["stale"]]
    passed = not stale
    log.info("freshness (as of %s): %d sources, %d stale -> %s",
             as_of, len(results), len(stale), "PASS" if passed else "STALE")
    return {"as_of": as_of.isoformat(), "passed": passed,
            "n_stale": len(stale), "results": results}
