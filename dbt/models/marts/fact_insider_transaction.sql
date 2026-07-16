-- The central fact: one row per Form 4 transaction = an insider -> company edge.
-- Foreign keys to dim_person (insider), dim_company (issuer), dim_date. Measures:
-- shares, price, signed_shares (net-position sign) and gross_value.
--
-- Incremental on transaction_id with a 90-day filing-date lookback: new and
-- late-arriving filings within the window merge in (delete+insert); anything
-- restated later than that is caught by a --full-refresh (weekly retrain DAG).
{{ config(
    materialized='incremental',
    unique_key='transaction_id',
    incremental_strategy='delete+insert',
) }}

select
    t.transaction_id,
    t.accession_no,
    t.txn_seq,
    -- dimension foreign keys
    t.owner_cik                              as person_cik,
    t.issuer_cik                             as company_cik,
    to_char(t.transaction_date, 'YYYYMMDD')::int as date_key,
    -- degenerate / descriptive attributes
    t.is_derivative,
    t.security_title,
    t.transaction_code,
    t.acquired_disposed,
    t.direct_indirect,
    t.transaction_date,
    -- measures
    t.shares,
    t.price_per_share,
    t.signed_shares,
    t.gross_value,
    t.shares_owned_following
from {{ ref('stg_form4_transaction') }} t
{% if is_incremental() %}
where t.filing_date >= (select coalesce(max(transaction_date), '1900-01-01'::date) - 90 from {{ this }})
{% endif %}
