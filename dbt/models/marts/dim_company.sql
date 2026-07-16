-- Company dimension (conformed). Grain: one row per CIK. Includes every curated
-- universe member plus any company that appears as a Form 4 issuer -- e.g. a
-- portfolio company an in-universe institution reports a >10% stake in. So
-- in_universe is a descriptive attribute, not a filter, and the fact's issuer FK
-- always resolves.
with companies as (
    select cik, ticker, title, module, in_universe
    from {{ ref('stg_company') }}
),

referenced_issuers as (
    -- companies that appear as a Form 4 issuer or a 13F filing manager
    select distinct issuer_cik as cik
    from {{ ref('stg_form4_transaction') }}
    where issuer_cik is not null
    union
    select distinct manager_cik
    from {{ ref('stg_form13f_holding') }}
    where manager_cik is not null
)

select
    c.cik           as company_cik,
    c.ticker,
    c.title         as company_name,
    c.module,
    c.in_universe
from companies c
where c.in_universe
   or c.cik in (select cik from referenced_issuers)
