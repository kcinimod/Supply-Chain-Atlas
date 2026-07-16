"""ingestion/edgar_client.py: URL builders + index/submissions parsing.

Network is never touched: the module's `session` injection point receives a
fake whose .get() serves canned responses.
"""
from __future__ import annotations

import datetime as dt

from ingestion import edgar_client


class FakeResponse:
    def __init__(self, *, text="", json_data=None, status=200):
        self.status_code = status
        self.text = text
        self._json = json_data

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeSession:
    """Serves responses keyed by URL; any unknown URL is a test failure."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        assert url in self.responses, f"unexpected URL fetched: {url}"
        return self.responses[url]


# --- URL builders -------------------------------------------------------------
def test_submissions_url():
    assert edgar_client.submissions_url(320193).endswith("/submissions/CIK0000320193.json")
    assert edgar_client.submissions_url(
        320193, "CIK0000320193-submissions-001.json"
    ).endswith("/submissions/CIK0000320193-submissions-001.json")


def test_daily_index_url_quarters():
    assert edgar_client.daily_index_url(dt.date(2024, 1, 15)).endswith(
        "/daily-index/2024/QTR1/master.20240115.idx")
    assert edgar_client.daily_index_url(dt.date(2024, 5, 1)).endswith(
        "/daily-index/2024/QTR2/master.20240501.idx")
    assert edgar_client.daily_index_url(dt.date(2024, 12, 31)).endswith(
        "/daily-index/2024/QTR4/master.20241231.idx")


def test_accession_from_filename():
    assert edgar_client.accession_from_filename(
        "edgar/data/320193/0000320193-24-000123.txt") == "0000320193-24-000123"


def test_primary_doc_url():
    url = edgar_client.primary_doc_url(320193, "0000320193-24-000123", "doc4.xml")
    assert url == ("https://www.sec.gov/Archives/edgar/data/320193/"
                   "000032019324000123/doc4.xml")
    assert edgar_client.primary_doc_url(320193, "0000320193-24-000123", None) is None
    assert edgar_client.primary_doc_url(320193, "0000320193-24-000123", "") is None


def test_full_submission_url():
    assert edgar_client.full_submission_url(320193, "0000320193-24-000123") == (
        "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123.txt")


# --- daily index parsing --------------------------------------------------------
def test_fetch_daily_index_parses_master_idx(fixtures_dir):
    day = dt.date(2024, 1, 15)
    idx_text = (fixtures_dir / "master.20240115.idx").read_text(encoding="utf-8")
    session = FakeSession({edgar_client.daily_index_url(day): FakeResponse(text=idx_text)})

    rows = edgar_client.fetch_daily_index(day, session=session)

    assert len(rows) == 4                      # header/preamble/short lines skipped
    assert rows[0] == {
        "cik": 320193,
        "company": "Apple Inc.",
        "form": "4",
        "filing_date": "20240115",
        "accession": "0000320193-24-000006",
    }
    assert rows[-1]["form"] == "SC 13G/A"
    assert rows[-1]["accession"] == "0000102909-24-000456"


def test_fetch_daily_index_missing_day_returns_empty():
    """SEC Archives answer 403 for a not-yet-published day -> treated as no index."""
    day = dt.date(2024, 1, 13)
    session = FakeSession({edgar_client.daily_index_url(day): FakeResponse(status=403)})
    assert edgar_client.fetch_daily_index(day, session=session) == []


# --- submissions API parsing -----------------------------------------------------
def test_iter_company_filings_recent_and_shards():
    cik = 320193
    recent = {
        "accessionNumber": ["0000320193-24-000006", "0000320193-24-000001"],
        "form": ["4", "10-K"],
        "filingDate": ["2024-01-15", "2024-01-02"],
        "primaryDocument": ["doc4.xml", "aapl-10k.htm"],
    }
    # Older shard blocks may omit primaryDocument entirely.
    shard = {
        "accessionNumber": ["0000320193-99-000009"],
        "form": ["8-K"],
        "filingDate": ["1999-03-01"],
    }
    session = FakeSession({
        edgar_client.submissions_url(cik): FakeResponse(json_data={
            "name": "Apple Inc.",
            "filings": {"recent": recent,
                        "files": [{"name": "CIK0000320193-submissions-001.json"}]},
        }),
        edgar_client.submissions_url(cik, "CIK0000320193-submissions-001.json"):
            FakeResponse(json_data=shard),
    })

    rows = list(edgar_client.iter_company_filings(cik, session=session))

    assert [r["accession"] for r in rows] == [
        "0000320193-24-000006", "0000320193-24-000001", "0000320193-99-000009"]
    assert rows[0]["form"] == "4"
    assert rows[0]["primary_document"] == "doc4.xml"
    assert rows[2]["primary_document"] is None   # missing block -> None, not KeyError
    assert len(session.calls) == 2


def test_company_name_from_submissions():
    cik = 1045810
    session = FakeSession({
        edgar_client.submissions_url(cik): FakeResponse(json_data={"name": "NVIDIA CORP"}),
    })
    assert edgar_client.company_name_from_submissions(cik, session=session) == "NVIDIA CORP"
