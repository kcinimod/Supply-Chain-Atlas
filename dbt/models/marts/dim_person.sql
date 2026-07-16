-- Person dimension. Grain: one row per reporting-owner CIK (EDGAR assigns every
-- insider a CIK, so entity resolution is mostly a CIK dedupe). Current name/role
-- come from the person's most-recent filing; name_variant_count surfaces the
-- name-spelling variation that motivates the resolution layer.
with tx as (
    select *
    from {{ ref('stg_form4_transaction') }}
    where owner_cik is not null
),

current_attrs as (
    select
        owner_cik,
        owner_name,
        is_director,
        is_officer,
        is_ten_pct_owner,
        is_other_relation,
        officer_title,
        row_number() over (
            partition by owner_cik
            order by transaction_date desc nulls last, accession_no desc
        ) as rn
    from tx
),

agg as (
    select
        owner_cik,
        count(distinct owner_name)  as name_variant_count,
        count(distinct issuer_cik)  as company_count,
        count(*)                    as transaction_count,
        min(transaction_date)       as first_seen,
        max(transaction_date)       as last_seen
    from tx
    group by owner_cik
)

select
    c.owner_cik            as person_cik,
    c.owner_name           as person_name,
    c.is_director,
    c.is_officer,
    c.is_ten_pct_owner,
    c.is_other_relation,
    c.officer_title,
    a.name_variant_count,
    a.company_count,
    a.transaction_count,
    a.first_seen,
    a.last_seen
from current_attrs c
join agg a using (owner_cik)
where c.rn = 1
