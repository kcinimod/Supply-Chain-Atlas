-- Thin cleaning view over the LLM-extracted supply-chain silver table.
select
    rel_id,
    source_accession as accession_no,
    supplier_cik,
    fiscal_year,
    customer_name_raw,
    customer_cik,
    pct_of_revenue,
    is_named,
    is_resolved,
    source_quote
from {{ source('atlas', 'supply_relationship') }}
