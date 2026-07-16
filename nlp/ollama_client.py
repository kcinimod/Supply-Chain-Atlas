"""Thin local-Ollama client: schema-constrained chat + embeddings. No SDK, just
the local HTTP API -- keeps the overlay dependency-free and offline."""
from __future__ import annotations

import json

import requests

from nlp import config


def chat_json(prompt: str, schema: dict, *, model: str | None = None,
              timeout: int = 180) -> dict:
    """Call the local model with a JSON schema. Ollama constrains decoding to the
    schema, so the reply is always valid JSON matching it."""
    resp = requests.post(
        f"{config.OLLAMA_URL}/api/chat",
        json={"model": model or config.EXTRACT_MODEL, "stream": False,
              "format": schema, "options": {"temperature": 0},
              "messages": [{"role": "user", "content": prompt}]},
        timeout=timeout,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["message"]["content"])


def embed(text: str, *, model: str | None = None, timeout: int = 120) -> list[float]:
    """Return one embedding vector for the text (local nomic-embed-text)."""
    resp = requests.post(
        f"{config.OLLAMA_URL}/api/embed",
        json={"model": model or config.EMBED_MODEL, "input": text},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"][0]


def health() -> bool:
    try:
        requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
        return True
    except Exception:
        return False
