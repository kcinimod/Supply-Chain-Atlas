"""CLI for the event & alert layer.

    uv run python -m events detect [--window-days N]
    uv run python -m events alerts
    uv run python -m events run [--window-days N]     # detect + alerts
"""
from __future__ import annotations

import argparse
import logging
import sys

from events import detect, rules, store


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    parser = argparse.ArgumentParser(prog="events")
    sub = parser.add_subparsers(dest="command", required=True)

    p_det = sub.add_parser("detect", help="run change detectors")
    p_det.add_argument("--window-days", type=int, default=None)
    sub.add_parser("alerts", help="derive alerts from unprocessed events")
    p_run = sub.add_parser("run", help="detect + alerts")
    p_run.add_argument("--window-days", type=int, default=None)

    args = parser.parse_args(argv)
    failed = False
    if args.command in ("detect", "run"):
        results = detect.run(window_days=args.window_days)
        failed = any(v < 0 for v in results.values())
        print(f"events: {results}")
    if args.command in ("alerts", "run"):
        n = rules.run()
        print(f"alerts: {n} new")
    print(f"totals: {store.counts()}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
