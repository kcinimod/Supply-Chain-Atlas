-- Security dimension. Grain: one row per CUSIP. 13F holdings are keyed by CUSIP,
-- and mapping CUSIP -> company CIK has no free authoritative source, so this
-- dimension stands on its own for now (company resolution is a later slice).
-- name_variant_count surfaces the same-security name-spelling variation across
-- managers that any future CUSIP->company resolution has to contend with.
with h as (
    select *
    from {{ ref('stg_form13f_holding') }}
    where cusip is not null
),

current_name as (
    select
        cusip,
        name_of_issuer,
        title_of_class,
        row_number() over (
            partition by cusip
            order by period_of_report desc nulls last, filing_date desc
        ) as rn
    from h
),

agg as (
    select
        cusip,
        count(distinct name_of_issuer) as name_variant_count,
        count(distinct manager_cik)    as holder_count
    from h
    group by cusip
)

select
    c.cusip,
    c.name_of_issuer   as security_name,
    c.title_of_class,
    a.name_variant_count,
    a.holder_count
from current_name c
join agg a using (cusip)
where c.rn = 1
