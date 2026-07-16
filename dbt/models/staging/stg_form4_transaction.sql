-- Thin cleaning view over the parsed silver table: add a surrogate grain key and
-- two derived measures (signed shares, gross value) used by the fact.
with src as (
    select * from {{ source('atlas', 'form4_transaction') }}
)
select
    accession_no,
    txn_seq,
    accession_no || '-' || txn_seq::text          as transaction_id,
    is_derivative,
    issuer_cik,
    issuer_name,
    issuer_symbol,
    owner_cik,
    owner_name,
    is_director,
    is_officer,
    is_ten_pct_owner,
    is_other_relation,
    officer_title,
    security_title,
    transaction_date,
    transaction_code,
    shares,
    price_per_share,
    acquired_disposed,
    -- signed by direction so SUM(signed_shares) = net change in position
    case acquired_disposed
        when 'A' then shares
        when 'D' then -shares
    end                                            as signed_shares,
    shares * price_per_share                       as gross_value,
    shares_owned_following,
    direct_indirect,
    filing_date
from src
