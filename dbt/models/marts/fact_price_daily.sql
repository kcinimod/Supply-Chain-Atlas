-- Daily return fact: one row per (ticker, trading day) with the raw return,
-- the benchmark return, and the ABNORMAL return (ticker minus benchmark) that
-- reaction labels and actuals-backfill are measured in. Volume context
-- (20-day average, ratio) supports volume-spike features and alerts.
--
-- Incremental: bars only append, but window functions need history, so each
-- run SCANS a 60-day lookback and EMITS only the trailing 30 days
-- (delete+insert on price_id). The 30-day emit window always has >=20 prior
-- days in scan scope, so lag/avg recompute correctly at the boundary.
{{ config(
    materialized='incremental',
    unique_key='price_id',
    incremental_strategy='delete+insert',
    post_hook="create index if not exists idx_fpd_ticker_date on {{ this }} (ticker, trade_date)",
) }}

with px as (
    select ticker, trade_date, adj_close, volume
    from {{ ref('stg_price_daily') }}
    {% if is_incremental() %}
    where trade_date >= (select max(trade_date) - 60 from {{ this }})
    {% endif %}
),

calc as (
    select
        ticker,
        trade_date,
        adj_close,
        adj_close / nullif(lag(adj_close) over w, 0) - 1  as daily_return,
        volume,
        avg(volume) over (
            partition by ticker order by trade_date
            rows between 20 preceding and 1 preceding
        )                                                  as avg_volume_20d
    from px
    window w as (partition by ticker order by trade_date)
),

bench as (
    select trade_date, daily_return as benchmark_return
    from calc
    where ticker = '{{ var("benchmark_ticker") }}'
)

select
    c.ticker || '-' || to_char(c.trade_date, 'YYYYMMDD')  as price_id,
    c.ticker,
    c.trade_date,
    to_char(c.trade_date, 'YYYYMMDD')::int                as date_key,
    c.adj_close,
    c.daily_return,
    b.benchmark_return,
    c.daily_return - b.benchmark_return                   as abnormal_return,
    c.volume,
    c.avg_volume_20d,
    case when c.avg_volume_20d > 0
         then c.volume / c.avg_volume_20d end             as volume_ratio_20d
from calc c
left join bench b using (trade_date)
{% if is_incremental() %}
where c.trade_date > (select max(trade_date) - 30 from {{ this }})
{% endif %}
