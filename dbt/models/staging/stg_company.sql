select
    cik,
    ticker,
    title,
    module,
    in_universe
from {{ source('atlas', 'dim_company') }}
