"""Score a model's supply-chain extractions against the hand-labeled gold set.

Loads the curated gold file (`supply_gold.jsonl`: one object per filing with a
`gold` list of true facts {customer, pct, named}) and a model variant's
predictions from Postgres, then reports, per tier:

  - EDGE precision / recall  -- did we get the right named customers? (identity
    matched on the same normaliser production uses, so it's apples-to-apples)
  - PERCENTAGE accuracy       -- of correctly-matched edges, how many have the
    right pct (exact, and within +/-1 point) -- the axis we know is shakiest.

Tiers: `named` (any named customer) and `resolved` (named AND resolvable to an
in-universe CIK -- the tier the dashboard's solid-vs-dashed edges rest on).

Usage:
  uv run python -m nlp.gold.score                    # scores the live table
  uv run python -m nlp.gold.score --model llama      # scores a snapshot
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from db.pool import connection
from nlp import resolve, store

GOLD = Path(__file__).with_name("supply_gold.jsonl")

TABLES = {
    "qwen_orig": "supply_relationship_qwen",
    "llama": "supply_relationship_llama",
    "qwen_impr": "supply_relationship",
    "live": "supply_relationship",
}


def _load_gold() -> dict[str, list[dict]]:
    if not GOLD.exists():
        raise SystemExit(
            f"no gold file at {GOLD}\n"
            f"create it: run `python -m nlp.gold.dump_candidates`, curate the "
            f"review file, and save it as supply_gold.jsonl")
    by_acc: dict[str, list[dict]] = {}
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        by_acc[obj["accession"]] = obj.get("gold", [])
    return by_acc


def _predictions(table: str) -> dict[str, list[dict]]:
    with connection(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT source_accession, customer_name_raw, pct_of_revenue, is_named "
            f"FROM {table}"    # table is an internal constant, not user input
        ).fetchall()
    by_acc: dict[str, list[dict]] = {}
    for acc, name, pct, named in rows:
        by_acc.setdefault(acc, []).append(
            {"customer": name, "pct": float(pct) if pct is not None else None,
             "named": bool(named)})
    return by_acc


def _named_map(facts: list[dict], resolver: resolve.Resolver,
               resolved_only: bool) -> dict[str, dict]:
    """{normalised-name -> fact} for the named facts in one filing."""
    out: dict[str, dict] = {}
    for f in facts:
        if not f.get("named"):
            continue
        norm = resolve.normalize(f["customer"])
        if not norm:
            continue
        if resolved_only and resolver.resolve(f["customer"]) is None:
            continue
        out.setdefault(norm, f)
    return out


def _pct_close(a, b, tol: float = 1.0) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def _score_tier(gold: dict, preds: dict, resolver, resolved_only: bool) -> dict:
    tp = fp = fn = 0
    pct_scored = pct_exact = pct_within1 = 0
    for acc, gfacts in gold.items():
        g = _named_map(gfacts, resolver, resolved_only)
        p = _named_map(preds.get(acc, []), resolver, resolved_only)
        for norm, gf in g.items():
            if norm in p:
                tp += 1
                if gf.get("pct") is not None:      # only score pct where truth states one
                    pct_scored += 1
                    pf_pct = p[norm].get("pct")
                    if pf_pct == gf["pct"]:
                        pct_exact += 1
                    if _pct_close(pf_pct, gf["pct"]):
                        pct_within1 += 1
            else:
                fn += 1
        for norm in p:
            if norm not in g:
                fp += 1
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and (precision + recall) else float("nan"))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "pct_scored": pct_scored, "pct_exact": pct_exact,
            "pct_within1": pct_within1}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="nlp.gold.score")
    ap.add_argument("--model", choices=list(TABLES), default="live",
                    help="which extraction table to score (default: live)")
    args = ap.parse_args(argv)

    gold = _load_gold()
    preds = _predictions(TABLES[args.model])
    resolver = resolve.Resolver(store.companies())

    scored_accs = [a for a in gold if a in preds]
    print(f"\nGold set: {len(gold)} filings   Model: {args.model} "
          f"({TABLES[args.model]})   Overlap scored: {len(scored_accs)}\n")
    print(f"{'tier':<10}{'P':>7}{'R':>7}{'F1':>7}{'TP':>5}{'FP':>5}{'FN':>5}"
          f'{"pct=":>8}{"pct~1":>7}')
    for tier, resolved_only in (("named", False), ("resolved", True)):
        m = _score_tier(gold, preds, resolver, resolved_only)
        pe = f"{m['pct_exact']}/{m['pct_scored']}" if m["pct_scored"] else "-"
        pw = f"{m['pct_within1']}/{m['pct_scored']}" if m["pct_scored"] else "-"
        print(f"{tier:<10}{m['precision']:>7.2f}{m['recall']:>7.2f}{m['f1']:>7.2f}"
              f"{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}{pe:>8}{pw:>7}")
    print("\nP=precision R=recall  |  pct= exact-match, pct~1 within +/-1 point "
          "(of correctly-matched edges)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
