"""Market-data source: daily OHLCV bars for universe tickers + benchmark.

Second concrete Source after EDGAR — proves the multi-source seam. Flow per
run (idempotent, incremental, laptop-off tolerant):

    1. incremental window per run: (max(trade_date) in price_daily) + 1 .. today
    2. yfinance batch download (one HTTP session, all tickers)
    3. bronze: the raw run snapshot lands in the Parquet lake
       (data/lake/prices/dt=YYYY-MM-DD/prices.parquet — columnar, partitioned)
    4. silver: upsert into price_daily (PK ticker+trade_date → re-runs no-op)
    5. checkpoint 'market_prices' advances to the max ingested date

A machine that slept for days simply gets a wider window on the next run.
Per-ticker error isolation: one delisted/renamed symbol never sinks the run.
"""
from __future__ import annotations

import datetime as dt
import logging

from ingestion import config, store, universe

log = logging.getLogger(__name__)


def _window() -> tuple[dt.date, dt.date]:
    """Incremental fetch window. Uses the actual table high-water mark rather
    than the checkpoint so a manual DB restore self-corrects."""
    with_default = dt.date.fromisoformat(config.PRICE_HISTORY_START)
    last = store.max_price_date()
    start = (last + dt.timedelta(days=1)) if last else with_default
    return start, dt.date.today()


def run(start: str | None = None) -> int:
    """Fetch bars from `start` (default: incremental) through today."""
    import pandas as pd
    import yfinance as yf

    tickers = universe.price_tickers()
    if not tickers:
        log.warning("no universe tickers; run load-companies first")
        return 0

    win_start, win_end = _window()
    if start:
        win_start = dt.date.fromisoformat(start)
    if win_start > win_end:
        log.info("prices: already caught up (next date %s)", win_start)
        store.set_checkpoint("market_prices", (win_start - dt.timedelta(days=1)).isoformat(),
                             "ok (caught up)", 0)
        return 0

    log.info("prices: fetching %d tickers, %s..%s", len(tickers), win_start, win_end)
    # yfinance end is exclusive; +1 day includes today's bar once it exists.
    frame = yf.download(
        tickers=tickers,
        start=win_start.isoformat(),
        end=(win_end + dt.timedelta(days=1)).isoformat(),
        auto_adjust=False,
        group_by="ticker",
        threads=True,
        progress=False,
    )
    if frame is None or frame.empty:
        log.info("prices: no bars in window (market closed / too early)")
        store.set_checkpoint("market_prices", win_start.isoformat(), "ok (no bars)", 0)
        return 0

    rows: list[tuple] = []
    failures = 0
    for ticker in tickers:
        try:
            sub = frame[ticker] if len(tickers) > 1 else frame
            sub = sub.dropna(subset=["Close"])
            for trade_date, bar in sub.iterrows():
                rows.append((
                    ticker,
                    trade_date.date(),
                    _num(bar.get("Open")), _num(bar.get("High")),
                    _num(bar.get("Low")), _num(bar.get("Close")),
                    _num(bar.get("Adj Close")),
                    int(bar["Volume"]) if pd.notna(bar.get("Volume")) else None,
                ))
        except (KeyError, TypeError) as exc:
            failures += 1
            log.error("prices: %s failed: %s", ticker, exc)

    if not rows:
        store.set_checkpoint("market_prices", win_start.isoformat(),
                             f"ok (0 rows, {failures} ticker failures)", 0)
        return 0

    _write_bronze_parquet(rows)
    n = store.upsert_prices(rows)
    max_date = max(r[1] for r in rows)
    status = "ok" if failures == 0 else f"{failures} ticker failures"
    store.set_checkpoint("market_prices", max_date.isoformat(), status, n)
    log.info("prices: %d bars upserted through %s (%d ticker failures)",
             n, max_date, failures)
    return n


def _num(v) -> float | None:
    import pandas as pd
    return float(v) if v is not None and pd.notna(v) else None


def _write_bronze_parquet(rows: list[tuple]) -> None:
    """Bronze snapshot of this run, columnar + date-partitioned. The lake is
    replayable history; Postgres is the serving copy."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    out_dir = config.LAKE_DIR / "prices" / f"dt={dt.date.today().isoformat()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cols = list(zip(*rows))
    table = pa.table({
        "ticker": cols[0],
        "trade_date": cols[1],
        "open": cols[2], "high": cols[3], "low": cols[4], "close": cols[5],
        "adj_close": cols[6],
        "volume": cols[7],
    })
    path = out_dir / "prices.parquet"
    pq.write_table(table, path, compression="snappy")
    log.info("prices: bronze snapshot %s (%d rows)", path, len(rows))
