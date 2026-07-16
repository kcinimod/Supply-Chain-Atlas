-- Date dimension. A contiguous day spine spanning the observed transaction dates,
-- so time-series queries (insider activity by month/quarter) can join cleanly and
-- gaps (no-activity days) are still represented.
with fact_dates as (
    select transaction_date as d
    from {{ ref('stg_form4_transaction') }}
    where transaction_date is not null
    union all
    select period_of_report
    from {{ ref('stg_form13f_holding') }}
    where period_of_report is not null
    union all
    select as_of_date
    from {{ ref('stg_sched13dg_stake') }}
    where as_of_date is not null
    union all
    -- v2: the price fact joins this spine too, so it must span trading days.
    select trade_date
    from {{ ref('stg_price_daily') }}
),

bounds as (
    select min(d) as lo, max(d) as hi
    from fact_dates
),

spine as (
    select generate_series(lo, hi, interval '1 day')::date as date_day
    from bounds
)

select
    date_day,
    to_char(date_day, 'YYYYMMDD')::int      as date_key,
    extract(year    from date_day)::int     as year,
    extract(quarter from date_day)::int     as quarter,
    extract(month   from date_day)::int     as month,
    to_char(date_day, 'Mon')                as month_name,
    extract(day     from date_day)::int     as day_of_month,
    extract(isodow  from date_day)::int     as iso_dow,
    to_char(date_day, 'Dy')                 as day_name,
    extract(isodow from date_day) >= 6      as is_weekend
from spine
