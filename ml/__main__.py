"""CLI front door for the ML core (two stages).

Stage 1 — 8-K substance classifier (the filter):
    uv run python -m ml items          # build/refresh the label layer
    uv run python -m ml train          # train + register (+ maybe promote champion)
    uv run python -m ml score          # production path: score new 8-Ks
    uv run python -m ml eval           # health check over a recent slice
    uv run python -m ml drift          # PSI vs FROZEN train baseline (exit 1 = fail)

Stage 2 — market-reaction model (the loop):
    uv run python -m ml reaction-train    # (event x horizon) -> register + promote
    uv run python -m ml reaction-predict  # score new events across all horizons
    uv run python -m ml reaction-eval     # backfill actuals + per-horizon metrics

Idempotent throughout; every consumer loads models:/<name>@champion.
"""
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()


def _setup_logging() -> None:
    # MLflow prints emoji run-links; Windows consoles default to cp1252 and
    # would crash the CLI *after* a successful train. Force UTF-8 stdout.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_items(_args) -> int:
    from ml import items
    r = items.run()
    print(f"items: {r['written']} labelled 8-Ks "
          f"({r['material']} material / {r['routine']} routine / {r['unknown']} no-items); "
          f"{r['skipped']} not-yet-backfilled skipped")
    return 0


def cmd_train(_args) -> int:
    from ml import model
    r = model.train()
    print(f"train: registered {model_name()} v{r['version']}  "
          f"roc_auc={r['roc_auc']:.3f} f1={r['f1']:.3f} "
          f"precision={r['precision']:.3f} recall={r['recall']:.3f} "
          f"(n_train={int(r['n_train'])}, n_test={int(r['n_test'])}, "
          f"material_rate={r['material_rate']:.2f})")
    return 0


def cmd_eval(_args) -> int:
    from ml import model
    r = model.evaluate()
    print(f"eval: v{r['version']} over {int(r['n_eval'])} recent 8-Ks  "
          f"roc_auc={r['roc_auc']:.3f} f1={r['f1']:.3f}")
    return 0


def cmd_drift(_args) -> int:
    from ml import model
    r = model.drift()
    status = "FAIL" if not r["passed"] else ("WARN" if r["warn"] else "PASS")
    print(f"drift: v{r['version']} PSI={r['psi']:.3f} -> {status}")
    return 0 if r["passed"] else 1


def cmd_score(_args) -> int:
    from ml import model
    n = model.score_new()
    print(f"score: {n} new 8-K(s) scored")
    return 0


def cmd_reaction_train(_args) -> int:
    from ml import reaction
    r = reaction.train()
    hit_by_h = {k: f"{v:.3f}" for k, v in r.items() if k.startswith("hit_rate_")}
    print(f"reaction-train: v{r['version']} roc_auc={r['roc_auc']:.3f} "
          f"hit_rate={r['hit_rate']:.3f} brier={r['brier']:.3f} "
          f"promoted={r['promoted']} per-horizon={hit_by_h}")
    return 0


def cmd_reaction_predict(_args) -> int:
    from ml import reaction
    n = reaction.predict()
    print(f"reaction-predict: {n} new prediction rows")
    return 0


def cmd_reaction_eval(_args) -> int:
    from ml import reaction
    r = reaction.evaluate()
    print(f"reaction-eval: v{r['version']} backfilled {r['backfilled']} actuals; "
          + "; ".join(f"{h}: hit={m['hit_rate']:.2f} brier={m['brier']:.3f} n={int(m['n'])}"
                      for h, m in r["horizons"].items()))
    return 0


def model_name() -> str:
    from ml import config
    return config.MODEL_NAME


def main(argv=None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="ml")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, fn in (("items", cmd_items), ("train", cmd_train),
                     ("eval", cmd_eval), ("drift", cmd_drift),
                     ("score", cmd_score),
                     ("reaction-train", cmd_reaction_train),
                     ("reaction-predict", cmd_reaction_predict),
                     ("reaction-eval", cmd_reaction_eval)):
        sub.add_parser(name).set_defaults(func=fn)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        logging.getLogger("ml").exception("command failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
