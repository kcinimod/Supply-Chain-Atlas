-- Thin cleaning view over the market-source silver table. Bars with no close
-- are non-observations (half-holidays, vendor gaps) and are dropped here so
-- every downstream return calculation can trust adj_close.
select
    ticker,
    trade_date,
    open,
    high,
    low,
    close,
    coalesce(adj_close, close) as adj_close,
    volume,
    source,
    loaded_at
from {{ source('atlas', 'price_daily') }}
where close is not null
