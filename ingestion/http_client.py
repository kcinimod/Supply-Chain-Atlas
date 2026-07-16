"""Robust HTTP: a shared session with SEC's required User-Agent, a global rate
limiter under the 10 req/s ceiling, and retry-with-exponential-backoff on
transient errors only. The session is injectable so tests never hit the network.
"""
from __future__ import annotations

import threading
import time

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ingestion import config

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryableHTTPError(Exception):
    """A transient status worth retrying (rate-limit / server blip)."""


class _RateLimiter:
    """Minimum-interval limiter: guarantees <= rps requests/second, process-wide."""

    def __init__(self, rps: float) -> None:
        self._min_interval = 1.0 / rps if rps > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


_limiter = _RateLimiter(config.RATE_LIMIT_RPS)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    return s


_session = make_session()


@retry(
    retry=retry_if_exception_type(RetryableHTTPError),
    wait=wait_exponential(multiplier=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)
def request(url: str, *, session: requests.Session | None = None,
            allow_missing: set[int] | None = None) -> requests.Response | None:
    """GET with rate-limiting + retry. Returns None when the status is in
    allow_missing. SEC's Archives return 403 (not 404) for a non-existent key,
    so daily-index callers treat {403, 404} as "no index that day"."""
    sess = session or _session
    _limiter.acquire()
    resp = sess.get(url, timeout=config.REQUEST_TIMEOUT)
    if resp.status_code in RETRYABLE_STATUS:
        raise RetryableHTTPError(f"{resp.status_code} for {url}")
    if allow_missing and resp.status_code in allow_missing:
        return None
    resp.raise_for_status()  # non-retryable 4xx -> raise immediately, no retry
    return resp


def get_json(url: str, *, session: requests.Session | None = None):
    return request(url, session=session).json()


def get_text(url: str, *, session: requests.Session | None = None,
             allow_missing: set[int] | None = None) -> str | None:
    resp = request(url, session=session, allow_missing=allow_missing)
    return resp.text if resp is not None else None


def get_bytes(url: str, *, session: requests.Session | None = None,
              allow_missing: set[int] | None = None) -> bytes | None:
    resp = request(url, session=session, allow_missing=allow_missing)
    return resp.content if resp is not None else None
