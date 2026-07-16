-- Thin cleaning view over parsed 13D/G stakes: surrogate key + an as-of date
-- (the event date if present, else the filing date) used for stake history.
select
    accession_no,
    stake_seq,
    accession_no || '-' || stake_seq::text as stake_id,
    submission_type,
    source_format,
    filer_cik,
    filer_name,
    subject_cik,
    subject_name,
    cusip,
    reporting_person_name,
    class_percent,
    aggregate_shares,
    sole_voting,
    shared_voting,
    sole_dispositive,
    shared_dispositive,
    is_amendment,
    filing_date,
    coalesce(event_date, filing_date) as as_of_date
from {{ source('atlas', 'sched13dg_stake') }}
