"""The serving API — the layer v1 never had.

FastAPI app exposing the graph, live feed, REAL alerts, change events,
per-horizon model outlook, and pipeline pulse. The dashboard is a static
front-end (dashboard/static/) served from here that bootstraps once and polls
/api/live — so "live" on the page means live.

    uv run uvicorn api.app:app --port 8000          # dev
    docker compose up -d api                         # stack

Design: read-only over the warehouse (the API borrows read-only pooled
connections), no auth (single-tenant local deployment; SaaS adds an auth
layer in front), JSON contracts kept flat and stable for the client.
"""
from __future__ import annotations

import logging
import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api import queries

log = logging.getLogger("api")

STATIC_DIR = pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"

app = FastAPI(title="Supply Chain Atlas API", version="2.0.0")


@app.get("/health")
def health() -> dict:
    from db.pool import connection
    with connection(readonly=True) as conn:
        conn.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/bootstrap")
def bootstrap() -> dict:
    return queries.bootstrap()


@app.get("/api/live")
def live() -> dict:
    return queries.live()


@app.get("/api/outlook/{ticker}")
def outlook(ticker: str) -> dict:
    result = queries.company_outlook(ticker)
    if not result["predictions"]:
        raise HTTPException(404, f"no predictions for {ticker!r}")
    return result


# --- static dashboard ---------------------------------------------------------
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(STATIC_DIR / "index.html"))
