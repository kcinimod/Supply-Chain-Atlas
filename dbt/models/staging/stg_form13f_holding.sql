-- Thin cleaning view over the parsed 13F silver table: add a surrogate grain key.
select
    accession_no,
    holding_seq,
    accession_no || '-' || holding_seq::text as holding_id,
    manager_cik,
    manager_name,
    period_of_report,
    name_of_issuer,
    title_of_class,
    cusip,
    value_reported,
    value_usd,
    shares,
    shares_type,
    investment_discretion,
    voting_sole,
    voting_shared,
    voting_none,
    filing_date
from {{ source('atlas', 'form13f_holding') }}
