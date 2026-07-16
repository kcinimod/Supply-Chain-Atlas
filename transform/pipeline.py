"""Form 4 extract+parse+load, bounded per run with per-item error isolation.

For each unparsed universe Form 4 we fetch the *raw* ownership XML (Phase 1
stored SEC's XSL-*rendered* HTML, which is display-only), archive it to the
raw_xml bronze root for reproducible re-parsing, parse it, and upsert the
transactions. Every filing — success, empty, or error — gets a parse-log row so
re-runs skip it and failures are auditable.
"""
from __future__ import annotations

import logging
import re

from ingestion import archive, config, edgar_client, http_client
from transform import exhibit21, form4, form13f, sched13dg, store

log = logging.getLogger(__name__)

# SEC renders Form 4 XML through an XSL stylesheet at .../xslF345X0N/doc.xml;
# stripping that path segment yields the underlying machine-readable XML.
_XSL_SEG = re.compile(r"/xsl[^/]+/")


def _raw_xml_url(cik: int, accession: str, primary_doc_url: str | None) -> str:
    if primary_doc_url:
        return _XSL_SEG.sub("/", primary_doc_url)
    # No primary doc recorded: fall back to the full submission text.
    return edgar_client.full_submission_url(cik, accession)


def _to_rows(f4: form4.Form4, *, accession: str, filing_date) -> list[dict]:
    """Flatten a parsed filing to transaction rows, attributed to the primary
    reporting owner (first). Multi-owner filings are rare; the owner_count in the
    parse log flags them."""
    owner = f4.owners[0] if f4.owners else None
    rows: list[dict] = []
    for seq, t in enumerate(f4.transactions):
        rows.append({
            "accession_no": accession,
            "txn_seq": seq,
            "is_derivative": t.is_derivative,
            "issuer_cik": f4.issuer_cik,
            "issuer_name": f4.issuer_name,
            "issuer_symbol": f4.issuer_symbol,
            "owner_cik": owner.cik if owner else None,
            "owner_name": owner.name if owner else None,
            "is_director": owner.is_director if owner else None,
            "is_officer": owner.is_officer if owner else None,
            "is_ten_pct_owner": owner.is_ten_pct_owner if owner else None,
            "is_other_relation": owner.is_other if owner else None,
            "officer_title": owner.officer_title if owner else None,
            "security_title": t.security_title,
            "transaction_date": t.transaction_date,
            "transaction_code": t.transaction_code,
            "shares": t.shares,
            "price_per_share": t.price_per_share,
            "acquired_disposed": t.acquired_disposed,
            "shares_owned_following": t.shares_owned_following,
            "direct_indirect": t.direct_indirect,
            "filing_date": filing_date,
        })
    return rows


def run(limit: int | None = None) -> dict[str, int]:
    limit = limit or config.DOC_FETCH_LIMIT
    todo = store.select_unparsed_form4(limit)

    parsed = txns = empty = failures = 0
    for accession, cik, form_type, filing_date, primary_url in todo:
        url = _raw_xml_url(cik, accession, primary_url)
        try:
            content = http_client.get_bytes(url, allow_missing={403, 404})
            if content is None:  # document genuinely gone from Archives
                store.log_parse(accession, status="error", txn_count=None,
                                owner_count=None, error="missing (403/404)",
                                xml_path=None)
                failures += 1
                continue

            xml_path, _sha, _n = archive.write_document(
                accession, cik, form_type, filing_date, content,
                base_dir=config.RAW_XML_DIR)
            f4 = form4.parse(content)
            rows = _to_rows(f4, accession=accession, filing_date=filing_date)
            store.load_transactions(rows)
            store.log_parse(accession, status="ok", txn_count=len(rows),
                            owner_count=len(f4.owners), error=None,
                            xml_path=xml_path)
            parsed += 1
            txns += len(rows)
            if not rows:
                empty += 1
        except Exception as exc:  # isolate: one bad filing never kills the run
            failures += 1
            log.error("form4 parse FAILED %s (%s): %s", accession, url, exc)
            store.log_parse(accession, status="error", txn_count=None,
                            owner_count=None, error=str(exc)[:500], xml_path=None)

    log.info("form4 transform: %d filings parsed (%d empty), %d transactions, "
             "%d failures (of %d attempted)", parsed, empty, txns, failures, len(todo))
    return {"parsed": parsed, "transactions": txns, "empty": empty,
            "failures": failures, "attempted": len(todo)}


def _holding_rows(f13: form13f.Filing13F, *, accession: str, manager_cik: int,
                  filing_date) -> list[dict]:
    rows: list[dict] = []
    for seq, h in enumerate(f13.holdings):
        rows.append({
            "accession_no": accession,
            "holding_seq": seq,
            "manager_cik": manager_cik,
            "manager_name": f13.manager_name,
            "period_of_report": f13.period_of_report,
            "name_of_issuer": h.name_of_issuer,
            "title_of_class": h.title_of_class,
            "cusip": h.cusip,
            "value_reported": h.value_reported,
            "value_usd": form13f.value_to_usd(h.value_reported, filing_date),
            "shares": h.shares,
            "shares_type": h.shares_type,
            "investment_discretion": h.investment_discretion,
            "voting_sole": h.voting_sole,
            "voting_shared": h.voting_shared,
            "voting_none": h.voting_none,
            "filing_date": filing_date,
        })
    return rows


def run_13f(limit: int | None = None) -> dict[str, int]:
    limit = limit or config.DOC_FETCH_LIMIT
    todo = store.select_unparsed_form13f(limit)

    parsed = holdings = empty = failures = 0
    for accession, cik, form_type, filing_date, _primary_url in todo:
        # The full-submission .txt bundles the cover XML and the info table, so a
        # single fetch yields both (the info-table filename varies by filer).
        url = edgar_client.full_submission_url(cik, accession)
        try:
            content = http_client.get_bytes(url, allow_missing={403, 404})
            if content is None:
                store.log_parse_13f(accession, status="error", holding_count=None,
                                    error="missing (403/404)", xml_path=None)
                failures += 1
                continue

            f13 = form13f.parse(content)
            rows = _holding_rows(f13, accession=accession, manager_cik=cik,
                                 filing_date=filing_date)
            # Archive just the parsed info table (not the whole .txt) for reproducibility.
            table_xml = form13f._slice(content, "informationTable")
            xml_path = None
            if table_xml is not None:
                xml_path, _sha, _n = archive.write_document(
                    accession, cik, form_type, filing_date, table_xml,
                    base_dir=config.RAW_XML_DIR)

            store.load_holdings(rows)
            store.log_parse_13f(accession, status="ok", holding_count=len(rows),
                                error=None, xml_path=xml_path)
            parsed += 1
            holdings += len(rows)
            if not rows:
                empty += 1
        except Exception as exc:
            failures += 1
            log.error("form13f parse FAILED %s (%s): %s", accession, url, exc)
            store.log_parse_13f(accession, status="error", holding_count=None,
                                error=str(exc)[:500], xml_path=None)

    log.info("form13f transform: %d filings parsed (%d empty), %d holdings, "
             "%d failures (of %d attempted)", parsed, empty, holdings, failures, len(todo))
    return {"parsed": parsed, "holdings": holdings, "empty": empty,
            "failures": failures, "attempted": len(todo)}


def _submission_dir_url(cik: int, accession: str) -> str:
    return f"{config.SEC_BASE}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/"


def run_exhibit21(limit: int | None = None) -> dict[str, int]:
    limit = limit or config.DOC_FETCH_LIMIT
    todo = store.select_unparsed_10k(limit)

    parsed = subs_total = no_exhibit = failures = 0
    for accession, cik, form_type, filing_date, _primary_url in todo:
        dir_url = _submission_dir_url(cik, accession)
        try:
            index = http_client.get_json(dir_url + "index.json")
            filenames = [it["name"] for it in index["directory"]["item"]]
            doc_name = exhibit21.find_exhibit_doc(filenames)
            if doc_name is None:
                store.log_parse_ex21(accession, status="no_exhibit",
                                     subsidiary_count=0, error=None,
                                     doc_name=None, xml_path=None)
                no_exhibit += 1
                continue

            content = http_client.get_bytes(dir_url + doc_name)
            pairs = exhibit21.parse(content)
            rows = [{
                "accession_no": accession, "sub_seq": seq, "parent_cik": cik,
                "parent_name": None, "subsidiary_name": name,
                "jurisdiction": juris, "filing_date": filing_date,
            } for seq, (name, juris) in enumerate(pairs)]

            xml_path, _sha, _n = archive.write_document(
                accession, cik, form_type, filing_date, content,
                base_dir=config.RAW_XML_DIR)
            store.load_subsidiaries(rows)
            status = "ok" if rows else "no_exhibit"
            store.log_parse_ex21(accession, status=status,
                                 subsidiary_count=len(rows), error=None,
                                 doc_name=doc_name, xml_path=xml_path)
            if rows:
                parsed += 1
                subs_total += len(rows)
            else:
                no_exhibit += 1
        except Exception as exc:
            failures += 1
            log.error("exhibit21 parse FAILED %s (%s): %s", accession, dir_url, exc)
            store.log_parse_ex21(accession, status="error", subsidiary_count=None,
                                 error=str(exc)[:500], doc_name=None, xml_path=None)

    log.info("exhibit21 transform: %d filings parsed, %d subsidiaries, "
             "%d no-exhibit, %d failures (of %d attempted)",
             parsed, subs_total, no_exhibit, failures, len(todo))
    return {"parsed": parsed, "subsidiaries": subs_total,
            "no_exhibit": no_exhibit, "failures": failures, "attempted": len(todo)}


def run_13dg(limit: int | None = None) -> dict[str, int]:
    limit = limit or config.DOC_FETCH_LIMIT
    todo = store.select_unparsed_13dg(limit)

    parsed = stakes_total = failures = 0
    for accession, cik, form_type, filing_date in todo:
        url = edgar_client.full_submission_url(cik, accession)
        try:
            content = http_client.get_bytes(url, allow_missing={403, 404})
            if content is None:
                store.log_parse_13dg(accession, status="error", stake_count=None,
                                     source_format=None, error="missing (403/404)",
                                     xml_path=None)
                failures += 1
                continue

            f = sched13dg.parse(content)
            rows = [{
                "accession_no": accession, "stake_seq": seq,
                "submission_type": f.submission_type or form_type,
                "source_format": f.source_format,
                "filer_cik": f.filer_cik, "filer_name": f.filer_name,
                "subject_cik": f.subject_cik, "subject_name": f.subject_name,
                "cusip": f.cusip, "reporting_person_name": s.reporting_person_name,
                "class_percent": s.class_percent, "aggregate_shares": s.aggregate_shares,
                "sole_voting": s.sole_voting, "shared_voting": s.shared_voting,
                "sole_dispositive": s.sole_dispositive,
                "shared_dispositive": s.shared_dispositive,
                "event_date": f.event_date, "filing_date": filing_date,
                "is_amendment": form_type.endswith("/A"),
            } for seq, s in enumerate(f.stakes)]

            # Archive the structured XML slice when present (legacy text stays as
            # its Phase 1 bronze copy).
            xml_path = None
            if f.source_format == "xml":
                xml_slice = sched13dg._slice(content, "edgarSubmission")
                if xml_slice is not None:
                    xml_path, _s, _n = archive.write_document(
                        accession, cik, form_type, filing_date, xml_slice,
                        base_dir=config.RAW_XML_DIR)

            store.load_stakes(rows)
            store.log_parse_13dg(accession, status="ok", stake_count=len(rows),
                                 source_format=f.source_format, error=None,
                                 xml_path=xml_path)
            parsed += 1
            stakes_total += len(rows)
        except Exception as exc:
            failures += 1
            log.error("sched13dg parse FAILED %s (%s): %s", accession, url, exc)
            store.log_parse_13dg(accession, status="error", stake_count=None,
                                 source_format=None, error=str(exc)[:500],
                                 xml_path=None)

    log.info("sched13dg transform: %d filings parsed, %d stakes, %d failures "
             "(of %d attempted)", parsed, stakes_total, failures, len(todo))
    return {"parsed": parsed, "stakes": stakes_total,
            "failures": failures, "attempted": len(todo)}
