"""CLI for Phase 6 (supply-chain NLP overlay).

    uv run python -m nlp extract [--limit N]   # 10-K free text -> supplier->customer facts
    uv run python -m nlp embed   [--limit N]   # local embeddings of disclosures -> pgvector
    uv run python -m nlp edges                 # print resolved in-universe edges

Requires a local Ollama (llama3.2:3b + nomic-embed-text). Idempotent.
"""
from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

load_dotenv()


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")


def cmd_extract(args) -> int:
    from nlp import pipeline
    r = pipeline.run_extract(limit=args.limit)
    print(f"extract: {r['filings']} filings, {r['facts']} facts, {r['failures']} failures")
    print(f"  totals: {r['relationships']} relationships across {r['suppliers']} suppliers "
          f"({r['named']} named, {r['resolved']} resolved to in-universe CIK)")
    return 0


def cmd_embed(args) -> int:
    from nlp import pipeline
    r = pipeline.run_embed(limit=args.limit)
    print(f"embed: {r['embedded']} disclosures embedded ({r['embeddings']} total in pgvector)")
    return 0


def cmd_edges(_args) -> int:
    from nlp import store
    edges = store.resolved_edges()
    print(f"resolved in-universe supplier -> customer edges: {len(edges)}")
    for supplier, customer, pct, raw in edges:
        pct_s = f"{pct:.1f}%" if pct is not None else "  n/a"
        print(f"  {supplier:>6} -> {customer:<6} {pct_s:>6}  (as: {raw})")
    return 0


def main(argv=None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="nlp")
    sub = parser.add_subparsers(dest="command", required=True)
    p_ex = sub.add_parser("extract"); p_ex.add_argument("--limit", type=int, default=20)
    p_ex.set_defaults(func=cmd_extract)
    p_em = sub.add_parser("embed"); p_em.add_argument("--limit", type=int, default=500)
    p_em.set_defaults(func=cmd_embed)
    sub.add_parser("edges").set_defaults(func=cmd_edges)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        logging.getLogger("nlp").exception("command failed: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
