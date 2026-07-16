"""Phase 6 config: local Ollama models + the extraction contract.

Both models are LOCAL (Ollama). The extraction model is small on purpose -- the
task is a short passage -> a couple of fields, and Ollama's schema-constrained
decoding guarantees valid JSON, so a 3B model is reliable. Swap either constant
for a larger model if accuracy ever needs it.
"""
from __future__ import annotations

import os
import re

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
EXTRACT_MODEL = os.environ.get("NLP_EXTRACT_MODEL", "qwen2.5:3b")
EMBED_MODEL = os.environ.get("NLP_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768

# The structured-output schema Ollama constrains the model to.
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "customers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "pct_of_revenue": {"type": ["number", "null"]},
                    "named": {"type": "boolean"},
                    "quote": {"type": "string"},
                },
                "required": ["name", "pct_of_revenue", "named", "quote"],
            },
        }
    },
    "required": ["customers"],
}

EXTRACT_PROMPT = (
    "You read an excerpt from a company's 10-K annual report and extract "
    "customer-concentration facts: every customer the company says accounts for a "
    "percentage of its revenue or net sales.\n"
    "Extract one fact for EACH such customer -- especially when the customer is "
    "named. Whenever the filer gives a real company name (e.g. 'NVIDIA', 'Applied "
    "Materials', 'Lockheed Martin'), set named=true and use that exact name. Set "
    "named=false ONLY when the filer withholds the name -- 'one customer', 'our "
    "largest customer', or an anonymised label like 'Customer A' -- and then use "
    "name='unnamed'.\n"
    "- pct_of_revenue is the number only (no % sign), or null if not stated.\n"
    "- quote is the short sentence the fact came from.\n"
    "- Only use facts stated in the excerpt; never invent a customer or a number.\n\n"
    "Example 1 -- named customers:\n"
    "Excerpt: 'In fiscal 2023, Applied Materials accounted for 32% and Lam Research "
    "for 28% of our revenue.'\n"
    "customers: [{\"name\":\"Applied Materials\",\"pct_of_revenue\":32,\"named\":true,"
    "\"quote\":\"Applied Materials accounted for 32% ... of our revenue\"},"
    "{\"name\":\"Lam Research\",\"pct_of_revenue\":28,\"named\":true,\"quote\":\"Lam "
    "Research for 28% of our revenue\"}]\n"
    "Example 2 -- withheld name:\n"
    "Excerpt: 'One customer represented approximately 15% of net sales.'\n"
    "customers: [{\"name\":\"unnamed\",\"pct_of_revenue\":15,\"named\":false,\"quote\":"
    "\"One customer represented approximately 15% of net sales\"}]\n\n"
    "EXCERPT:\n"
)

# Passage retrieval: a window is a candidate if it mentions a customer near a
# percent near a revenue word. Keeps the LLM prompt small and on-topic.
PCT_RE = re.compile(r"\d{1,2}(?:\.\d)?\s*%")
CUSTOMER_RE = re.compile(r"customer|customers|client", re.I)
REVENUE_RE = re.compile(r"revenue|net sales|\bsales\b|total", re.I)

# Names that are placeholders, not companies -> never count as "named"/resolvable.
# NOTE: "the" must NOT be a bare placeholder marker — legal names like
# "The Boeing Company" start with it. Only demote "the <generic-noun>" forms.
ANON_RE = re.compile(
    r"^\s*(customer\s+[a-z0-9]|one\b|a\b|an\b|certain\b|two\b|three\b|"
    r"several\b|various\b|unnamed|single|largest|top\b|major|our\b|"
    r"the\s+(?:customer|company|companies|government|distributor|oem|"
    r"u\.?s\.?\s+government|united\s+states)\b)", re.I)

PASSAGE_WINDOW = 240      # chars each side of a % match
MAX_PASSAGES = 8
MAX_PROMPT_CHARS = 6000
