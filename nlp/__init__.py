"""Phase 6 -- the supply-chain NLP overlay (the project's namesake).

Supplier -> customer relationships exist nowhere in EDGAR's structured data; they
live only in 10-K free text (the customer-concentration disclosure). This package
extracts them with a LOCAL open-weights LLM (Qwen 2.5 via Ollama, schema-
constrained JSON so output is always valid), resolves named customers to
in-universe CIKs, and embeds the disclosure passages locally (nomic-embed-text)
into pgvector. Everything runs offline -- the filings never leave the machine.

Extraction is deliberately honest about its own partiality: it records whether a
customer was named or anonymised, and whether it resolved to a known company.
"""
