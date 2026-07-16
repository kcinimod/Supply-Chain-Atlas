# Phase 8 — Serving: The API and the Dashboard That Stopped Pretending

> **Learning write-up & presentation notes.** v1 had **no HTTP API anywhere**:
> the dashboard was a build script that baked query results into a static HTML
> file, its alert rail was four hardcoded rows, and its "live" dot was
> cosmetic. v2 serves everything through FastAPI over the warehouse, and the
> dashboard polls it — so "live" on the page means live.
>
> **What this phase demonstrates (course rubric):** the serving end of the
> end-to-end pipeline; the *"Dashboard Updates: show how new data triggers
> refreshed charts, predictions, or alerts"* demo; Docker-packaged model/API
> serving; and contract-shaped reads over the star schema.

---

## 1. What this phase is for

Every layer below exists so a user can ask: *what changed, does it matter, and
what does the model think happens next?* Serving is where the warehouse, the
event layer, the ML loop, and the fast path converge onto one screen:

```
Postgres (marts · filing_event · alert · reaction_prediction · trade_window)
    │   read-only pooled connections
    ▼
FastAPI (api/app.py + api/queries.py)
    /health              liveness + a real DB round-trip
    /api/bootstrap       the graph substrate (fetched once per session)
    /api/live            feed · alerts · pulse · changes · outlook (polled)
    /api/outlook/{t}     per-horizon reaction predictions for one ticker
    │
    ▼
dashboard/static (app.js · index.html · style.css)
    bootstraps once → polls /api/live → change badges · outlook chips · Changes view
```

---

## 2. The API — two payload families, on purpose

`api/queries.py` splits reads by *cadence*, which is the design decision worth
stating:

- **`bootstrap()`** — the heavy graph substrate: companies, ownership stakes
  (current SCD2 rows), insider edges, subsidiaries, supply edges (named tiers),
  filing counts, recent filings, and **change flags** (which companies had
  which event types in the last 45 days). This changes on the daily batch
  tick, so the client fetches it **once** and on manual refresh.
- **`live()`** — the parts worth polling: latest filings feed, **real alerts**
  (the phase-7 table, all sources), recent change events, per-ticker
  **model outlook** (latest `reaction_prediction` per horizon), pipeline pulse
  (checkpoint/freshness status), and the latest Flink `trade_window` rows as
  live market context. Cheap queries only — a poll tick must never hurt the
  warehouse.

*Rejected:* one `/api/everything` (re-ships the graph on every poll), and
GraphQL (two clients' worth of flexibility for one client's worth of need).
The contracts are deliberately **flat and stable** — the front-end is the only
consumer today, but the JSON shapes are the SaaS product surface tomorrow.

Everything is **read-only by construction**: the API borrows pooled
connections with `SET TRANSACTION READ ONLY` (phase 1's guard), so no bug in
the serving layer can mutate the warehouse. No auth — single-tenant local
deployment; a SaaS puts an auth proxy *in front* rather than teaching every
endpoint about users.

## 3. The dashboard — change is a first-class citizen

The v1 SVG graph visual system survives (per `DESIGN_PATTERNS.md`); what
changed is that every number on screen is now queried, and **change flagging**
is the organizing feature — the UI equivalent of the v2 product thesis:

- **Change badges** (`Δ MM-DD`) on company cards wherever a `filing_event`
  landed inside the 45-day badge window, with tooltips naming the event types.
- **Outlook chips** — the focused company shows the reaction model's latest
  per-horizon `P(up)` as chips (3d/1w/1m/3m), each labelled as a model estimate
  ("not investment advice" — honesty in the UI, not just the docs).
- **A Changes view** — the `filing_event` stream as a feed, beside the alert
  rail. Alerts carry their severity and source, so a `stream`-sourced alert
  visibly beat the batch path to the screen.
- **Polling, not WebSockets** — a deliberate rejection: filings arrive at
  minutes-cadence at best, so a poll tick is indistinguishable from push in
  practice and removes a connection-lifecycle failure mode from a laptop that
  sleeps nightly. The fast path's latency budget is spent where it matters
  (feed → Kafka → alert table), not on the last hop to the browser.

## 4. Packaging — one image, reused *(Docker Compose demo)*

The `api` Compose service **reuses the Airflow image** as a plain Python
runtime (`entrypoint: python -m uvicorn api.app:app`), with the repo
bind-mounted at `/opt/atlas`:

> *"The Airflow image already contains every library the project imports —
> building a second near-identical image would double the maintenance surface
> for zero isolation gain on a single-owner laptop. One image, four roles:
> scheduler, dag-processor, api-server, and the serving API (and the streaming
> consumers reuse it too)."*

`restart: unless-stopped` + a `/health` healthcheck (which performs a real DB
`SELECT 1`, not just process liveness) means the API rides through the nightly
outage like everything else. Port 8000 serves both the JSON API and the static
dashboard (`/` → `index.html`, `/static/*` mounted from `dashboard/static`).

*Rejected:* baking the code into the image (every edit would rebuild;
bind-mount + restart is the right trade for a dev-shaped deployment — stated
as such, with "bake at CI" as the obvious SaaS hardening).

---

## 5. What we built and verified

- `GET /health` → `{"status":"ok"}` with a live DB round-trip.
- `GET /api/bootstrap` → the full graph substrate (79 companies, stakes,
  insiders, subsidiaries, supply tiers, change flags).
- `GET /api/live` → alerts (6,085 and counting, all sources), feed, changes,
  outlook, pulse — polled by the dashboard.
- `GET /api/outlook/NVDA` → per-horizon `P(up)` rows from the champion's
  prediction log (404 when a ticker has no predictions — explicit, not empty).
- End-to-end: a filing on the live feed → stream alert row → visible on the
  dashboard within one poll tick; the daily batch adds its events, badges and
  refreshed outlooks after 09:30.

---

## 6. How to run

```bash
docker compose up -d api        # (part of the default stack)
open http://localhost:8000      # dashboard; /docs for the OpenAPI UI
curl -s localhost:8000/health
curl -s localhost:8000/api/outlook/NVDA | python -m json.tool
# dev, without Docker:
uv run uvicorn api.app:app --port 8000
```

---

## 7. What's next / with more time

- **Auth + multi-tenancy** in front of the API (the SaaS roadmap's first item).
- **Server-sent events** for the alert rail if a genuine push use case appears
  — the polling decision is cheap to revisit because the contract is stable.
- **Contract tests** — the JSON shapes are hand-kept in sync with `app.js`
  today; a schema (pydantic response models) would make drift a test failure.

---

*Key files: `api/app.py` (endpoints, static mount), `api/queries.py`
(bootstrap/live/outlook contracts), `dashboard/static/app.js` (badges, chips,
Changes view, polling), `docker-compose.yml` (`api` service reusing the
Airflow image), `db/pool.py` (read-only connections).*
