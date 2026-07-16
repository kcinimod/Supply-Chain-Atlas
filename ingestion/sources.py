"""Source registry: the multi-source seam.

Every data provider is a `Source` — a named ingest unit with its own cursor
row in `ingest_checkpoint` and its own bronze layout. Orchestration (Airflow)
and the CLI iterate the registry instead of knowing providers by name, so
adding a data source is: implement `ingest()`, register it here, done.

Current sources:
    edgar_poller    incremental daily-index CDC over SEC EDGAR (universe scope)
    edgar_backfill  full submissions-API history per universe company
    market_prices   daily OHLCV bars (yfinance) for universe tickers + benchmark
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class Source(Protocol):
    name: str
    description: str

    def ingest(self, **kwargs) -> int: ...


@dataclass(frozen=True)
class _FnSource:
    name: str
    description: str
    _fn: Callable[..., int]

    def ingest(self, **kwargs) -> int:
        return self._fn(**kwargs)


def _edgar_poller(**kwargs) -> int:
    from ingestion import poller
    return poller.run(**kwargs)


def _edgar_backfill(**kwargs) -> int:
    from ingestion import backfill
    return backfill.run(**kwargs) or 0


def _market_prices(**kwargs) -> int:
    from ingestion import market
    return market.run(**kwargs)


SOURCES: dict[str, Source] = {
    s.name: s
    for s in (
        _FnSource("edgar_poller", "SEC EDGAR daily-index CDC poll", _edgar_poller),
        _FnSource("edgar_backfill", "SEC EDGAR submissions-API history", _edgar_backfill),
        _FnSource("market_prices", "Daily OHLCV bars (yfinance)", _market_prices),
    )
}


def get(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(f"unknown source {name!r}; known: {sorted(SOURCES)}") from None
