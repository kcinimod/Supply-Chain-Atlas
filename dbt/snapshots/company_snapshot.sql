{#
  SCD Type 2 history for company reference attributes (ticker / name / module).
  The `check` strategy versions a row whenever any check_col changes between runs:
  dbt closes the old version (sets dbt_valid_to) and opens a new current one. This
  is how a ticker rebrand (e.g. FB -> META) or a company rename is preserved as
  history rather than overwritten -- the amendment/supersession story, applied to
  slowly-changing dimensions.
#}
{% snapshot company_snapshot %}
{{
  config(
    target_schema='snapshots',
    unique_key='cik',
    strategy='check',
    check_cols=['ticker', 'title', 'module']
  )
}}
select
    cik,
    ticker,
    title,
    module,
    in_universe
from {{ source('atlas', 'dim_company') }}
where in_universe
{% endsnapshot %}
