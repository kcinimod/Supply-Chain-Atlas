-- Supplier -> customer edge, extracted from 10-K free text by a local LLM.
-- Grain: one disclosed customer-concentration fact per filing (history preserved).
--
-- This is the ONLY edge whose source is prose, so it is also the only one with
-- an explicit reliability tier -- the honest counterpart to the solid/dashed
-- distinction in the dashboard:
--   resolved         named customer matched to an in-universe dim_company CIK  (a real edge)
--   named_unresolved a real company name that isn't in the universe            (name-only node)
--   unnamed          "one customer" / "Customer A" -- concentration but no counterparty
-- supplier_cik always resolves; customer_cik is populated only when resolved.
--
-- A supplier that files a 10-K every year discloses the same customer edge each
-- year, so the raw grain holds one row PER FILING YEAR. is_current marks the most
-- recent fiscal year per (supplier, customer) relationship -- the dashboard/API
-- read only is_current so a repeat disclosure shows as ONE current edge, while the
-- prior-year rows stay in the fact for trajectory. Relationship identity is the
-- resolved CIK when known, else the raw name; 'unnamed' rows are keyed together
-- but is_current keeps EVERY disclosure from the latest year (a filer may name two
-- unnamed >10% customers in one 10-K), only dropping older years.
with s as (
    select * from {{ ref('stg_supply_relationship') }}
),

flagged as (
    select
        *,
        coalesce(cast(customer_cik as text), customer_name_raw) as customer_key,
        max(fiscal_year) over (
            partition by supplier_cik, coalesce(cast(customer_cik as text), customer_name_raw)
        ) as latest_fy
    from s
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
    -- current = newest filing year for this relationship (null-year kept only if
    -- the relationship has no dated filing at all)
    (fiscal_year is not distinct from latest_fy) as is_current,
    source_quote
from flagged
