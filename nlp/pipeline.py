"""Orchestrates the overlay: supplier 10-K -> passages -> LLM facts -> resolve
-> store, and a separate local-embedding pass. Per-filing error isolation so one
bad document never sinks the run."""
from __future__ import annotations

import logging

from nlp import config, extract, ollama_client, resolve, store, text

log = logging.getLogger(__name__)


def run_extract(limit: int = 20) -> dict:
    if not ollama_client.health():
        raise RuntimeError(f"Ollama not reachable at {config.OLLAMA_URL} -- is it running?")

    resolver = resolve.Resolver(store.companies())
    todo = store.suppliers_to_extract(limit)
    filings = facts_total = failures = 0

    for accession, supplier_cik, ticker, doc_path, filing_date in todo:
        try:
            passages = text.concentration_passages(text.read_text(doc_path))
            if not passages:
                store.write_log(accession, supplier_cik, 0, 0, 0,
                                config.EXTRACT_MODEL, "no_passage")
                filings += 1
                continue

            facts = extract.extract_facts(passages)
            fy = filing_date.year if filing_date else None
            rows, n_named, n_resolved = [], 0, 0
            for f in facts:
                cik = resolver.resolve(f["name"]) if f["is_named"] else None
                if cik == supplier_cik:   # a filer naming itself is not a customer edge
                    cik = None
                n_named += int(f["is_named"])
                n_resolved += int(cik is not None)
                rows.append((accession, supplier_cik, fy, f["name"], cik,
                             f["pct_of_revenue"], f["is_named"], cik is not None,
                             f["quote"]))
            store.write_relationships(rows)
            store.write_log(accession, supplier_cik, len(facts), n_named, n_resolved,
                            config.EXTRACT_MODEL, "ok")
            facts_total += len(facts)
            filings += 1
            log.info("extract %s (%s): %d facts, %d named, %d resolved",
                     ticker, accession, len(facts), n_named, n_resolved)
        except Exception as exc:  # isolate per filing
            failures += 1
            store.write_log(accession, supplier_cik, 0, 0, 0,
                            config.EXTRACT_MODEL, "error", str(exc)[:400])
            log.error("extract FAILED %s (%s): %s", ticker, accession, exc)

    return {"filings": filings, "facts": facts_total, "failures": failures, **store.counts()}


def run_embed(limit: int = 500) -> dict:
    if not ollama_client.health():
        raise RuntimeError(f"Ollama not reachable at {config.OLLAMA_URL}")
    todo = store.quotes_to_embed(limit)
    embedded = failures = 0
    for accession, supplier_cik, quote in todo:
        try:
            store.write_embedding(accession, supplier_cik, quote,
                                  ollama_client.embed(quote))
            embedded += 1
        except Exception as exc:
            failures += 1
            log.error("embed FAILED %s: %s", accession, exc)
    log.info("embed done: %d embedded, %d failures", embedded, failures)
    return {"embedded": embedded, "failures": failures, **store.counts()}
