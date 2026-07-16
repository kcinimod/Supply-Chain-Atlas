"""CLI front door for Phase 2 transform.

    uv run python -m transform form4 [--limit N]

Fetches raw ownership XML for unparsed universe Form 4 filings, parses them into
the form4_transaction silver table, and records a parse-log row per filing.
Idempotent and backfill-safe: re-runs only pick up not-yet-parsed filings.
"""
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_form4(args) -> int:
    from transform import pipeline, store
    result = pipeline.run(limit=args.limit)
    totals = store.counts()
    print(f"form4: parsed {result['parsed']} filings "
          f"({result['transactions']} transactions, {result['empty']} empty, "
          f"{result['failures']} failures) of {result['attempted']} attempted")
    print(f"  totals: {totals['transactions']} transactions across "
          f"{totals['filings_parsed']} filings ({totals['errors']} errors)")
    return 0


def cmd_form13f(args) -> int:
    from transform import pipeline, store
    result = pipeline.run_13f(limit=args.limit)
    totals = store.counts_13f()
    print(f"form13f: parsed {result['parsed']} filings "
          f"({result['holdings']} holdings, {result['empty']} empty, "
          f"{result['failures']} failures) of {result['attempted']} attempted")
    print(f"  totals: {totals['holdings']} holdings across "
          f"{totals['filings_parsed']} filings ({totals['errors']} errors)")
    return 0


def cmd_exhibit21(args) -> int:
    from transform import pipeline, store
    result = pipeline.run_exhibit21(limit=args.limit)
    totals = store.counts_ex21()
    print(f"exhibit21: parsed {result['parsed']} filings "
          f"({result['subsidiaries']} subsidiaries, {result['no_exhibit']} no-exhibit, "
          f"{result['failures']} failures) of {result['attempted']} attempted")
    print(f"  totals: {totals['subsidiaries']} subsidiaries across "
          f"{totals['filings_parsed']} filings "
          f"({totals['no_exhibit']} no-exhibit, {totals['errors']} errors)")
    return 0


def cmd_sched13dg(args) -> int:
    from transform import pipeline, store
    result = pipeline.run_13dg(limit=args.limit)
    totals = store.counts_13dg()
    print(f"sched13dg: parsed {result['parsed']} filings "
          f"({result['stakes']} stakes, {result['failures']} failures) "
          f"of {result['attempted']} attempted")
    print(f"  totals: {totals['stakes']} stakes across {totals['filings_parsed']} filings "
          f"({totals['xml']} structured-XML, {totals['errors']} errors)")
    return 0


def main(argv=None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="transform")
    sub = parser.add_subparsers(dest="command", required=True)

    p_f4 = sub.add_parser("form4", help="parse Form 4 XML -> form4_transaction")
    p_f4.add_argument("--limit", type=int, default=None,
                      help="max filings to parse this run")

    p_f13 = sub.add_parser("form13f", help="parse 13F-HR XML -> form13f_holding")
    p_f13.add_argument("--limit", type=int, default=None,
                       help="max filings to parse this run")

    p_ex21 = sub.add_parser("exhibit21",
                            help="parse 10-K Exhibit 21 -> exhibit21_subsidiary")
    p_ex21.add_argument("--limit", type=int, default=None,
                        help="max filings to parse this run")

    p_13dg = sub.add_parser("sched13dg",
                            help="parse Schedule 13D/G -> sched13dg_stake")
    p_13dg.add_argument("--limit", type=int, default=None,
                        help="max filings to parse this run")

    args = parser.parse_args(argv)
    return {"form4": cmd_form4, "form13f": cmd_form13f,
            "exhibit21": cmd_exhibit21, "sched13dg": cmd_sched13dg}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
