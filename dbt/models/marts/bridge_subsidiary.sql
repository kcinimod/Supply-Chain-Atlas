-- Parent -> subsidiary bridge (a "factless fact table": the row records that a
-- relationship EXISTS, with no numeric measure). Grain: one (10-K, subsidiary).
-- The parent resolves to dim_company; the subsidiary is a name-only node
-- (subsidiaries are private, so no CIK). is_latest_filing marks the rows from
-- each parent's most recent 10-K, since the subsidiary list is refiled yearly.
with s as (
    select * from {{ ref('stg_exhibit21_subsidiary') }}
),

latest as (
    select parent_cik, max(filing_date) as latest_filing_date
    from s
    group by parent_cik
)

select
    s.subsidiary_id,
    s.accession_no,
    s.parent_cik            as company_cik,   -- FK to dim_company (the registrant)
    s.subsidiary_name,
    s.jurisdiction,
    s.filing_date,
    (s.filing_date = l.latest_filing_date) as is_latest_filing
from s
join latest l using (parent_cik)
