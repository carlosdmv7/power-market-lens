-- One row per zone, load kind and interval: the latest version.
-- Same deduplication rule as the prices (ADR-003). Actual load and its
-- day-ahead forecast share the raw table; process_type tells them apart.

with source as (
    select * from {{ source('entsoe', 'total_load') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by bidding_zone_eic, process_type, period_start_utc, resolution_minutes
        order by _loaded_at desc, document_created_at desc
    ) = 1
)

select
    bidding_zone_eic,
    process_type,
    case process_type
        when 'A16' then 'actual'
        when 'A01' then 'day_ahead_forecast'
    end as load_kind,
    period_start_utc,
    period_start_utc + resolution_minutes * interval 1 minute as period_end_utc,
    resolution_minutes,
    load_mw,
    document_mrid,
    document_created_at,
    _loaded_at as loaded_at
from latest
