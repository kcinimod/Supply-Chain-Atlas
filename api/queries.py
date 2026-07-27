"""Warehouse reads behind the serving API.

Two payload families:
  bootstrap()  the graph substrate — companies, edges, per-company context.
               Changes on the daily batch tick; the client fetches it once
               and on manual refresh.
  live()       feed, REAL alerts, pulse, recent changes, outlook — the parts
               the dashboard polls. Cheap queries only.

v1 shipped these numbers baked into a static HTML file (and the alert rail
was four hardcoded rows). Every field here is queried live, including alerts,
change flags (filing_event) and per-horizon model outlook (reaction_prediction).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from db.pool import connection

CHANGE_WINDOW_DAYS = 45   # how far back a change still earns a badge


def _roles() -> dict[str, str]:
    from ingestion import universe
    return {m["ticker"]: m["side"]
            for m in universe.members() if m["ticker"] and m["side"] != "owner"}


def bootstrap() -> dict:
    roles = _roles()
    with connection(readonly=True) as c:
        comp = c.execute(
            "SELECT cik, ticker, coalesce(title, ticker), module FROM dim_company "
            "WHERE in_universe AND module IS NOT NULL").fetchall()
        stakes = c.execute(
            "SELECT company_cik, coalesce(nullif(reporting_person_name,''), filer_name), "
            "       class_percent "
            "FROM analytics.fact_ownership_stake "
            "WHERE is_current AND class_percent IS NOT NULL AND class_percent > 0").fetchall()
        insiders = c.execute(
            "SELECT f.company_cik, p.person_name, "
            "  coalesce(nullif(p.officer_title,''), "
            "    case when p.is_director then 'Director' else 'Insider' end), "
            "  p.transaction_count "
            "FROM analytics.fact_insider_transaction f "
            "JOIN analytics.dim_person p ON p.person_cik = f.person_cik").fetchall()
        subs = c.execute(
            "SELECT company_cik, count(*), "
            "  (array_agg(subsidiary_name ORDER BY subsidiary_name))[1:3] "
            "FROM analytics.bridge_subsidiary WHERE is_latest_filing GROUP BY 1").fetchall()
        # all three tiers reach the client: resolved edges draw between two
        # universe nodes; named_unresolved + unnamed have no in-universe
        # counterpart, so they surface as partner nodes in company view and as
        # an aggregate disclosure badge in universe view. is_current keeps one
        # edge per relationship (an annual filer re-discloses the same customer
        # every 10-K; older years live in the fact but must not double-draw).
        supply = c.execute(
            "SELECT supplier_cik, customer_cik, customer_name_raw, pct_of_revenue, tier "
            "FROM analytics.fact_supply_relationship WHERE is_current").fetchall()
        ownuni = c.execute(
            "SELECT filer_cik, company_cik, max(class_percent) "
            "FROM analytics.fact_ownership_stake "
            "WHERE is_current AND class_percent > 1 "
            "  AND filer_cik IN (SELECT cik FROM dim_company WHERE in_universe) "
            "  AND company_cik IN (SELECT cik FROM dim_company WHERE in_universe) "
            "GROUP BY 1, 2").fetchall()
        uni_ciks = [r[0] for r in comp]
        fil_counts = dict(c.execute(
            "SELECT cik, count(*) FROM raw_filing WHERE cik = ANY(%s) GROUP BY 1",
            (uni_ciks,)).fetchall())
        recent_fil = c.execute(
            "SELECT cik, to_char(filing_date,'MM-DD'), form_type FROM ("
            "  SELECT cik, filing_date, form_type, row_number() OVER "
            "    (PARTITION BY cik ORDER BY filing_date DESC NULLS LAST, accession_no DESC) rn "
            "  FROM raw_filing WHERE cik = ANY(%s)) t WHERE rn <= 6 ORDER BY cik",
            (uni_ciks,)).fetchall()
        # change flags: which companies had which event types recently
        change_rows = c.execute(
            "SELECT cik, event_type, max(event_date) "
            "FROM filing_event WHERE event_date >= current_date - %s "
            "GROUP BY cik, event_type", (CHANGE_WINDOW_DAYS,)).fetchall()

    companies = {}
    for cik, ticker, title, module in comp:
        companies[str(cik)] = {"t": ticker, "n": title,
                               "m": module, "r": roles.get(ticker, "owner")}

    def topn(rows, keyidx, validx, n, fields):
        grouped = defaultdict(list)
        for row in rows:
            grouped[str(row[0])].append(row)
        out = {}
        for cik, rs in grouped.items():
            rs = sorted(rs, key=lambda r: (r[validx] is not None, r[validx]), reverse=True)
            seen, picked = set(), []
            for r in rs:
                if r[keyidx] in seen:
                    continue
                seen.add(r[keyidx])
                picked.append([r[i] for i in fields])
                if len(picked) >= n:
                    break
            out[cik] = picked
        return out

    subs_by = {str(cik): {"n": cnt, "names": names} for cik, cnt, names in subs}
    ow_count, ins_count = defaultdict(int), defaultdict(set)
    for r in stakes:
        ow_count[str(r[0])] += 1
    for r in insiders:
        ins_count[str(r[0])].add(r[1])
    stats = {
        str(cik): {"ow": ow_count.get(str(cik), 0),
                   "ins": len(ins_count.get(str(cik), set())),
                   "sub": subs_by.get(str(cik), {}).get("n", 0),
                   "fil": fil_counts.get(cik, 0)}
        for cik, _t, _n, _m in comp
    }
    cfilings = defaultdict(list)
    for cik, d, form in recent_fil:
        cfilings[str(cik)].append([d, form])

    changes: dict[str, dict] = {}
    for cik, etype, latest in change_rows:
        entry = changes.setdefault(str(cik), {"types": [], "latest": None})
        entry["types"].append(etype)
        latest_s = latest.isoformat()
        if entry["latest"] is None or latest_s > entry["latest"]:
            entry["latest"] = latest_s

    return {
        "companies": companies,
        "stats": stats,
        "cfilings": cfilings,
        "stakes": {k: [[o, float(p)] for o, p in v]
                   for k, v in topn(stakes, 1, 2, 6, [1, 2]).items()},
        "insiders": topn(insiders, 1, 3, 6, [1, 2]),
        "subs": subs_by,
        "supply": [[str(a), str(b) if b else None, n,
                    float(p) if p is not None else None, t]
                   for a, b, n, p, t in supply],
        "ownuni": [[str(a), str(b), float(p)] for a, b, p in ownuni],
        "changes": changes,
        "changeWindowDays": CHANGE_WINDOW_DAYS,
    }


def live() -> dict:
    with connection(readonly=True) as c:
        feed = c.execute(
            "SELECT to_char(r.filing_date,'MM-DD'), coalesce(c.ticker,'-'), r.form_type, "
            "  left(coalesce(c.title, r.company_name), 22) "
            "FROM raw_filing r LEFT JOIN dim_company c ON c.cik = r.cik "
            "ORDER BY r.filing_date DESC, r.accession_no DESC LIMIT 12").fetchall()
        alerts = c.execute(
            "SELECT to_char(created_at,'MM-DD HH24:MI'), severity, rule, title, "
            "       coalesce(ticker, cik::text, ''), source "
            "FROM alert ORDER BY created_at DESC, alert_id DESC LIMIT 20").fetchall()
        top_stakes = c.execute(
            "SELECT c.ticker, round(s.class_percent,1), "
            "  coalesce(nullif(s.reporting_person_name,''), s.filer_name) "
            "FROM analytics.fact_ownership_stake s "
            "JOIN dim_company c ON c.cik = s.company_cik "
            "WHERE s.is_current AND s.class_percent BETWEEN 3 AND 100 "
            "ORDER BY s.class_percent DESC LIMIT 12").fetchall()
        recent_changes = c.execute(
            "SELECT e.event_id, to_char(e.event_date,'MM-DD'), e.event_type, "
            "       coalesce(e.ticker, e.cik::text), "
            "       left(coalesce(dc.title, e.ticker, ''), 26), e.details "
            "FROM filing_event e LEFT JOIN dim_company dc ON dc.cik = e.cik "
            "ORDER BY e.event_date DESC, e.event_id DESC LIMIT 30").fetchall()
        pulse = _pulse(c)
        outlook = _outlook(c)
        windows = c.execute(
            "SELECT symbol, to_char(max(window_end),'HH24:MI:SS'), "
            "       (array_agg(vwap ORDER BY window_start DESC))[1] "
            "FROM trade_window WHERE window_start > now() - interval '10 minutes' "
            "GROUP BY symbol ORDER BY symbol LIMIT 12").fetchall()

    return {
        "asOf": dt.datetime.now().strftime("%H:%M:%S"),
        "feed": [list(r) for r in feed],
        "alerts": [{"at": a, "severity": s, "rule": ru, "title": t,
                    "who": w, "source": src}
                   for a, s, ru, t, w, src in alerts],
        "topStakes": [[tk, float(p), o] for tk, p, o in top_stakes],
        "changesFeed": [{"id": i, "date": d, "type": ty, "ticker": tk,
                         "name": nm, "details": det}
                        for i, d, ty, tk, nm, det in recent_changes],
        "outlook": outlook,
        "pulse": pulse,
        "windows": [[s, t, float(v) if v is not None else None]
                    for s, t, v in windows],
    }


def _pulse(c) -> dict:
    mv = c.execute("SELECT max(model_version::int) FROM eightk_eval_metric").fetchone()[0]
    met = dict(c.execute(
        "SELECT metric, value FROM eightk_eval_metric "
        "WHERE eval_split='test' AND model_version=%s", (str(mv),)).fetchall()) if mv else {}
    psi = c.execute(
        "SELECT value FROM eightk_eval_metric WHERE eval_split='drift' "
        "AND metric='psi' ORDER BY evaluated_at DESC LIMIT 1").fetchone()
    n_filings = c.execute("SELECT count(*) FROM raw_filing").fetchone()[0]
    n_events = c.execute("SELECT count(*) FROM filing_event").fetchone()[0]
    n_alerts = c.execute("SELECT count(*) FROM alert").fetchone()[0]
    n_preds, n_actuals = c.execute(
        "SELECT count(*), count(actual_car) FROM reaction_prediction").fetchone()
    # latest per-horizon hit rate for the reaction champion
    hits = c.execute(
        "SELECT horizon, value FROM ("
        "  SELECT horizon, value, row_number() OVER "
        "    (PARTITION BY horizon ORDER BY evaluated_at DESC) rn "
        "  FROM reaction_eval_metric WHERE metric = 'hit_rate') t WHERE rn = 1").fetchall()
    edge_total = c.execute(
        "SELECT (SELECT count(*) FROM analytics.fact_insider_transaction)"
        " + (SELECT count(*) FROM analytics.fact_holding)"
        " + (SELECT count(*) FROM analytics.bridge_subsidiary)"
        " + (SELECT count(*) FROM analytics.fact_ownership_stake)"
        " + (SELECT count(*) FROM analytics.fact_supply_relationship WHERE is_resolved AND is_current)"
    ).fetchone()[0]
    return {
        "f1": round(float(met.get("f1", 0)), 2) if met else None,
        "auc": round(float(met.get("roc_auc", 0)), 2) if met else None,
        "psi": round(float(psi[0]), 2) if psi else None,
        "model": f"v{mv}" if mv else "-",
        "filings": n_filings,
        "events": n_events,
        "alerts": n_alerts,
        "predictions": n_preds,
        "actuals": n_actuals,
        "hitRates": {h: round(float(v), 2) for h, v in hits},
        "edges": edge_total,
    }


def _outlook(c) -> dict[str, dict[str, float]]:
    """ticker -> {horizon: P(up)} from each company's LATEST predicted event."""
    rows = c.execute(
        "SELECT ticker, horizon, proba_up FROM ("
        "  SELECT ticker, horizon, proba_up, row_number() OVER "
        "    (PARTITION BY ticker, horizon ORDER BY event_date DESC, event_id DESC) rn "
        "  FROM reaction_prediction) t WHERE rn = 1").fetchall()
    out: dict[str, dict[str, float]] = {}
    for ticker, horizon, proba in rows:
        out.setdefault(ticker, {})[horizon] = round(float(proba), 2)
    return out


def company_outlook(ticker: str) -> dict:
    """Per-horizon prediction history + filled actuals for one company."""
    with connection(readonly=True) as c:
        rows = c.execute(
            "SELECT rp.event_date, rp.horizon, rp.proba_up, rp.actual_car, "
            "       e.event_type "
            "FROM reaction_prediction rp JOIN filing_event e USING (event_id) "
            "WHERE rp.ticker = %s "
            "ORDER BY rp.event_date DESC, rp.horizon LIMIT 120",
            (ticker.upper(),)).fetchall()
    return {"ticker": ticker.upper(), "predictions": [
        {"date": d.isoformat(), "horizon": h, "probaUp": float(p),
         "actualCar": float(a) if a is not None else None, "eventType": et}
        for d, h, p, a, et in rows]}
