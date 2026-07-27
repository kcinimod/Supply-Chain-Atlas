"""ingestion/poller.py cursor safety.

Regression cover for the silent-data-loss bug: SEC returns 403 for a daily index
that is missing OR merely not published yet, and the scheduled run fires before
publish time. The poller used to accept that empty result as a completed day and
advance its cursor onto it, so the real index -- published minutes later -- was
never read again. 2026-07-10..07-23 lost 146 universe filings that way while the
checkpoint still reported 'ok'.

Network and DB are both faked; only the day-walk / cursor logic is under test.
"""
from __future__ import annotations

import datetime as dt

import pytest

from ingestion import poller

UNIVERSE = {320193}


def _filing(day: dt.date, form="4", accession=None):
    return {"cik": 320193, "company": "APPLE INC", "form": form,
            "filing_date": day.isoformat(),
            "accession": accession or f"acc-{day.isoformat()}"}


@pytest.fixture
def harness(monkeypatch):
    """Fake the three collaborators and record what the poller did."""
    state = {"checkpoint": None, "upserted": [], "fetched": []}

    def fake_fetch(day, *, session=None):
        state["fetched"].append(day)
        return state["index"].get(day, [])

    def fake_upsert(rows):
        state["upserted"].extend(rows)
        return len(rows)

    def fake_set(source, cursor_value, status, records):
        state["checkpoint"] = cursor_value

    monkeypatch.setattr(poller.edgar_client, "fetch_daily_index", fake_fetch)
    monkeypatch.setattr(poller.store, "universe_ciks", lambda: UNIVERSE)
    monkeypatch.setattr(poller.store, "upsert_filings", fake_upsert)
    monkeypatch.setattr(poller.store, "set_checkpoint", fake_set)
    monkeypatch.setattr(poller.store, "get_checkpoint", lambda s: None)
    return state


def _freeze_today(monkeypatch, day: dt.date):
    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            return day
    monkeypatch.setattr(poller.dt, "date", FrozenDate)


def test_unpublished_today_does_not_advance_cursor(harness, monkeypatch):
    """THE bug: today's index isn't published at run time. The cursor must stay
    behind it so the next run re-reads the day."""
    today = dt.date(2026, 7, 22)          # Wednesday
    _freeze_today(monkeypatch, today)
    harness["index"] = {}                  # 403 -> [] for every day

    poller.run(since="2026-07-22")

    assert harness["checkpoint"] is None, "cursor advanced over an unread day"
    assert harness["upserted"] == []


def test_day_is_reread_on_the_next_run(harness, monkeypatch):
    """Having not advanced, the retry picks the filings up once SEC publishes."""
    today = dt.date(2026, 7, 22)
    _freeze_today(monkeypatch, today)
    harness["index"] = {}
    poller.run(since="2026-07-22")
    assert harness["checkpoint"] is None

    # Next day's run: 07-22 is now published, and outside the grace window.
    later = dt.date(2026, 7, 23)
    _freeze_today(monkeypatch, later)
    harness["index"] = {dt.date(2026, 7, 22): [_filing(dt.date(2026, 7, 22), "8-K")]}
    poller.run(since="2026-07-22")

    assert [r["form_type"] for r in harness["upserted"]] == ["8-K"]
    assert harness["checkpoint"] == "2026-07-22", "should commit the day it read"


def test_weekend_is_skipped_not_retried(harness, monkeypatch):
    """A weekend never gets an index; stalling on it would wedge the poller."""
    today = dt.date(2026, 7, 20)           # Monday
    _freeze_today(monkeypatch, today)
    harness["index"] = {dt.date(2026, 7, 17): [_filing(dt.date(2026, 7, 17))]}

    poller.run(since="2026-07-17")         # Fri, Sat, Sun, Mon

    # Sat/Sun empty but advanced past; Monday is inside the grace window and
    # unread, so the cursor stops at Sunday.
    assert harness["checkpoint"] == "2026-07-19"
    assert dt.date(2026, 7, 18) in harness["fetched"]


def test_stale_empty_weekday_is_accepted_as_holiday(harness, monkeypatch):
    """Past the grace window an empty weekday is a real non-filing day, so the
    walk must move on rather than retry it forever."""
    today = dt.date(2026, 7, 24)
    _freeze_today(monkeypatch, today)
    # 07-20 empty (holiday), 07-21 has a filing.
    harness["index"] = {dt.date(2026, 7, 21): [_filing(dt.date(2026, 7, 21))]}

    poller.run(since="2026-07-20")

    assert harness["checkpoint"] == "2026-07-22", "grace-expired holiday blocked the walk"
    assert len(harness["upserted"]) == 1


def test_only_universe_and_target_forms_are_kept(harness, monkeypatch):
    today = dt.date(2026, 7, 24)
    _freeze_today(monkeypatch, today)
    day = dt.date(2026, 7, 21)
    harness["index"] = {day: [
        _filing(day, "8-K", "keep-8k"),
        _filing(day, "S-1", "drop-form"),          # not a target form
        {"cik": 999999, "company": "OTHER", "form": "4",
         "filing_date": day.isoformat(), "accession": "drop-cik"},
    ]}

    poller.run(since="2026-07-21")

    assert [r["accession_no"] for r in harness["upserted"]] == ["keep-8k"]
