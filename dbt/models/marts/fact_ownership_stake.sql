-- Beneficial-ownership stake fact: one row per (13D/G filing, reporting person)
-- for stakes IN a universe company (owner -> subject edge). FKs to dim_company
-- (the subject) and dim_date (as-of date). Measures: class_percent, shares,
-- voting/dispositive power.
--
-- SCD Type 2 by WINDOW FUNCTION (not a dbt snapshot): the 13D/G amendments are
-- already event-dated, so we reconstruct each (subject, filer) stake's history in
-- one pass -- each filing's valid_from is its as-of date and valid_to is the next
-- filing's as-of date (null = current). This is the counterpart to the company
-- snapshot: snapshots track drift going forward; window functions rebuild history
-- that already exists in dated events.
with s as (
    select * from {{ ref('stg_sched13dg_stake') }}
    where subject_cik is not null
),

-- keep only edges whose subject is a company in our graph
scoped as (
    select s.*
    from s
    join {{ ref('dim_company') }} dc on dc.company_cik = s.subject_cik
),

versioned as (
    select
        *,
        to_char(as_of_date, 'YYYYMMDD')::int as date_key,
        lead(as_of_date) over (
            partition by subject_cik, filer_cik
            order by as_of_date, accession_no
        ) as next_as_of
    from scoped
)

select
    stake_id,
    accession_no,
    submission_type,
    source_format,
    -- dimension foreign keys
    subject_cik            as company_cik,
    date_key,
    -- owner (name-only unless the filer is itself a universe entity)
    filer_cik,
    filer_name,
    reporting_person_name,
    -- measures
    class_percent,
    aggregate_shares,
    sole_voting,
    shared_voting,
    sole_dispositive,
    shared_dispositive,
    -- SCD Type 2 validity window
    as_of_date             as valid_from,
    next_as_of             as valid_to,
    (next_as_of is null)   as is_current,
    is_amendment
from versioned
