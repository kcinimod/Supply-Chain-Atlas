-- Thin passthrough of the universe-as-data table (v2). Downstream marts join
-- on ticker/cik to scope facts and label modules/sides.
select
    id,
    ticker,
    cik,
    title,
    module,
    side,
    active,
    added_at,
    updated_at
from {{ source('atlas', 'universe_member') }}
