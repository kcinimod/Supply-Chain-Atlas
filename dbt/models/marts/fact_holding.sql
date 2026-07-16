-- Institutional-holding fact: one row per 13F holding = an institution -> security
-- edge, as of a quarter-end. FKs to dim_company (the manager), dim_security
-- (CUSIP) and dim_date (period). Measures: value_usd, shares, voting authority.
-- Quarterly grain makes this a natural time series of each manager's book.
--
-- Incremental (the single largest fact, ~785k rows): a 200-day filing-date
-- lookback comfortably covers a quarter's late/amended 13Fs; older amendments
-- are picked up by the weekly --full-refresh.
{{ config(
    materialized='incremental',
    unique_key='holding_id',
    incremental_strategy='delete+insert',
) }}

select
    t.holding_id,
    t.accession_no,
    t.holding_seq,
    -- dimension foreign keys
    t.manager_cik                                 as company_cik,
    t.cusip,
    to_char(t.period_of_report, 'YYYYMMDD')::int  as date_key,
    -- descriptive attributes
    t.title_of_class,
    t.shares_type,
    t.investment_discretion,
    t.period_of_report,
    -- measures
    t.value_usd,
    t.shares,
    t.voting_sole,
    t.voting_shared,
    t.voting_none
from {{ ref('stg_form13f_holding') }} t
{% if is_incremental() %}
where t.filing_date >= (select coalesce(max(period_of_report), '1900-01-01'::date) - 200 from {{ this }})
{% endif %}
