"""Alert rule engine: derive user-facing alerts from detected events.

v1's dashboard alerts were four hardcoded HTML rows; these are real — every
alert traces to a filing_event (or a system check), carries a severity, and
dedupes on a natural key so re-runs never re-notify.

Severity policy (investment-signal lens):
    high   directly actionable relationship/ownership change
    watch  meaningful but needs context (small moves, clusters)
    info   awareness only (routine material 8-Ks)
"""
from __future__ import annotations

import logging

from events import store

log = logging.getLogger(__name__)


def _dedup_key(ev: dict, rule: str) -> str:
    return f"{rule}:{ev['cik']}:{ev['event_date']}:{ev.get('accession_no') or '-'}"


def _fmt_pct(v) -> str:
    return f"{v:.1f}%" if isinstance(v, (int, float)) else "undisclosed %"


def _alert_for_event(ev: dict, names: dict[int, str]) -> dict | None:
    d = ev["details"] or {}
    who = ev.get("ticker") or names.get(ev["cik"]) or str(ev["cik"])
    etype = ev["event_type"]

    if etype == "supply_edge_new":
        cust = d.get("customer_name", "?")
        return {
            "rule": "supply_edge_new", "severity": "high",
            "title": f"New supply edge — {who} → {cust} ({_fmt_pct(d.get('pct_of_revenue'))})",
            "body": (f"{who}'s 10-K discloses {cust} at "
                     f"{_fmt_pct(d.get('pct_of_revenue'))} of revenue — first appearance "
                     f"of this customer in the filing history."),
        }
    if etype == "supply_edge_changed":
        cust = d.get("customer_name", "?")
        delta = d.get("delta_pp")
        direction = "up" if (delta or 0) > 0 else "down"
        sev = "high" if abs(delta or 0) >= 5 else "watch"
        return {
            "rule": "supply_edge_changed", "severity": sev,
            "title": f"Supply concentration {direction} — {who} → {cust} "
                     f"({_fmt_pct(d.get('prev_pct'))} → {_fmt_pct(d.get('pct_of_revenue'))})",
            "body": f"Revenue concentration moved {delta:+.1f}pp vs the prior 10-K.",
        }
    if etype == "stake_change":
        filer = d.get("filer_name") or "an owner"
        crossed = d.get("crossed_levels") or []
        sev = "high" if any(lvl >= 10 for lvl in crossed) or abs(d.get("delta_pp") or 0) >= 5 \
            else "watch"
        moved = (f" ({d['prev_percent']:.1f}% → {d['class_percent']:.1f}%)"
                 if d.get("prev_percent") is not None
                 else f" ({d['class_percent']:.1f}%)")
        return {
            "rule": "stake_change", "severity": sev,
            "title": f"Ownership stake change — {filer} in {who}{moved}",
            "body": (f"Crossed {', '.join(f'{lvl:.0f}%' for lvl in crossed)}."
                     if crossed else "Stake moved materially between filings."),
        }
    if etype == "insider_cluster":
        n = d.get("n_insiders")
        sev = "high" if (n or 0) >= 5 else "watch"
        return {
            "rule": "insider_cluster", "severity": sev,
            "title": f"Insider cluster buying — {who} ({n} insiders)",
            "body": f"{n} distinct insiders made open-market purchases within "
                    f"{d.get('window_days', 30)} days.",
        }
    if etype == "material_8k":
        codes = ", ".join(d.get("item_codes") or [])
        proba = d.get("clf_proba")
        return {
            "rule": "material_8k", "severity": "info",
            "title": f"Material 8-K — {who} (items {codes})",
            "body": (f"Classifier P(material) = {proba:.2f}." if proba is not None
                     else "Labeled material by Item codes; not yet scored."),
        }
    return None


def run() -> int:
    """Derive alerts for every event that has none yet. Idempotent."""
    events = store.events_without_alerts()
    names = store.title_by_cik(sorted({e["cik"] for e in events}))
    alerts = []
    for ev in events:
        try:
            a = _alert_for_event(ev, names)
        except Exception as exc:  # per-event isolation
            log.error("rule engine failed on event %s: %s", ev.get("event_id"), exc)
            continue
        if a is not None:
            alerts.append({**a, "cik": ev["cik"], "ticker": ev.get("ticker"),
                           "event_id": ev["event_id"], "source": "batch",
                           "dedup_key": _dedup_key(ev, a["rule"])})
    n = store.insert_alerts(alerts)
    log.info("rules: %d event(s) examined, %d alert(s) created", len(events), n)
    return n


def system_alert(rule: str, severity: str, title: str, body: str, dedup_key: str) -> int:
    """Entry point for pipeline-health alerts (model decay, stale sources) —
    used by the eval DAG and asset checks."""
    return store.insert_alerts([{
        "rule": rule, "severity": severity, "title": title, "body": body,
        "source": "system", "dedup_key": dedup_key,
    }])
