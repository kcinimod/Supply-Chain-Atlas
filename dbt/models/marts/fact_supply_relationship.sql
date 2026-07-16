-- Supplier -> customer edge, extracted from 10-K free text by a local LLM.
-- Grain: one disclosed customer-concentration fact per filing.
--
-- This is the ONLY edge whose source is prose, so it is also the only one with
-- an explicit reliability tier -- the honest counterpart to the solid/dashed
-- distinction in the dashboard:
--   resolved         named customer matched to an in-universe dim_company CIK  (a real edge)
--   named_unresolved a real company name that isn't in the universe            (name-only node)
--   unnamed          "one customer" / "Customer A" -- concentration but no counterparty
-- supplier_cik always resolves; customer_cik is populated only when resolved.
with s as (
    select * from {{ ref('stg_supply_relationship') }}
)

select
    rel_id,
    accession_no,
    supplier_cik,                       -- FK to dim_company (the disclosing filer)
    customer_cik,                       -- FK to dim_company when resolved, else null
    customer_name_raw,
    fiscal_year,
    pct_of_revenue,
    is_named,
    is_resolved,
    case
        when is_resolved then 'resolved'
        when is_named    then 'named_unresolved'
        else 'unnamed'
    end as tier,
    source_quote
from s
