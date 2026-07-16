-- Thin cleaning view over the parsed Exhibit 21 silver table.
select
    accession_no,
    sub_seq,
    accession_no || '-' || sub_seq::text as subsidiary_id,
    parent_cik,
    subsidiary_name,
    jurisdiction,
    filing_date
from {{ source('atlas', 'exhibit21_subsidiary') }}
