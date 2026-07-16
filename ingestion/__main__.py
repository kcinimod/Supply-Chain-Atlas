"""CLI front door for ingestion.

    uv run python -m ingestion load-companies
    uv run python -m ingestion backfill [--limit N]
    uv run python -m ingestion poller [--since YYYY-MM-DD]
    uv run python -m ingestion fetch-docs [--limit N] [--form 4]
    uv run python -m ingestion prices [--start YYYY-MM-DD]

Each command isolates per-item failures and exits non-zero on hard failure so
the scheduler (Airflow) can detect it.
"""
from __future__ import annotations

import argparse
import logging
import sys

from ingestion import backfill, fetch_docs, poller, reference, store, universe


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_load_companies(_args) -> int:
    reference.load_reference()
    universe.seed_from_csv()
    resolved, unresolved = universe.resolve_and_apply()
    print(f"universe: {len(resolved)} tickers resolved, "
          f"{len(unresolved)} unresolved, {len(store.universe_ciks())} total members")
    if unresolved:
        print(f"  unresolved (check ticker): {', '.join(unresolved)}")
        return 1
    return 0


def cmd_prices(args) -> int:
    from ingestion import market
    n = market.run(start=args.start)
    print(f"price_daily: {n} bars ingested this run")
    return 0


def cmd_backfill(args) -> int:
    backfill.run(limit=args.limit)
    print(f"raw_filing now holds {store.count_filings()} filings")
    return 0


def cmd_poller(args) -> int:
    poller.run(since=args.since)
    print(f"raw_filing now holds {store.count_filings()} filings")
    return 0


def cmd_fetch_docs(args) -> int:
    fetch_docs.run(limit=args.limit, form_prefix=args.form)
    return 0


def cmd_freshness(args) -> int:
    import datetime as dt

    from ingestion import freshness
    as_of = dt.date.fromisoformat(args.as_of) if args.as_of else None
    r = freshness.check(as_of=as_of)
    print(f"freshness as of {r['as_of']}: "
          f"{'PASS' if r['passed'] else f'STALE ({r['n_stale']} source(s))'}")
    print(f"  {'source':<28} {'last filing':<12} {'days':>5} {'limit':>6}  status")
    for s in r["results"]:
        if s["no_data"]:
            print(f"  {s['source']:<28} {'(no data)':<12} {'':>5} {'':>6}  --")
            continue
        flag = "STALE" if s["stale"] else "ok"
        print(f"  {s['source']:<28} {str(s['last_filing']):<12} "
              f"{s['days_since']:>5} {s['threshold']:>6}  {flag}")
    return 0 if r["passed"] else 1


def main(argv=None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="ingestion")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("load-companies", help="seed reference + flag universe")

    p_back = sub.add_parser("backfill", help="submissions-API history for universe")
    p_back.add_argument("--limit", type=int, default=None,
                        help="only crawl the first N universe companies")

    p_poll = sub.add_parser("poller", help="incremental daily-index ingest")
    p_poll.add_argument("--since", default=None, help="override start date YYYY-MM-DD")

    p_docs = sub.add_parser("fetch-docs", help="download raw documents to bronze")
    p_docs.add_argument("--limit", type=int, default=None)
    p_docs.add_argument("--form", default=None, help="only this form prefix, e.g. 4")

    p_fresh = sub.add_parser("freshness", help="per-source gap/freshness check")
    p_fresh.add_argument("--as-of", dest="as_of", default=None,
                         help="evaluate as of this date YYYY-MM-DD (default today)")

    p_price = sub.add_parser("prices", help="daily OHLCV bars for universe + benchmark")
    p_price.add_argument("--start", default=None,
                         help="override incremental start date YYYY-MM-DD")

    args = parser.parse_args(argv)
    handler = {
        "load-companies": cmd_load_companies,
        "backfill": cmd_backfill,
        "poller": cmd_poller,
        "fetch-docs": cmd_fetch_docs,
        "freshness": cmd_freshness,
        "prices": cmd_prices,
    }[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
