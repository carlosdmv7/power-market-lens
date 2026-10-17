-- One row per production type, direction and interval: the latest version.
-- Same deduplication rule as the prices (ADR-003).

with source as (
    select * from {{ source('entsoe', 'generation_per_type') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by
            bidding_zone_eic, psr_type, flow_direction, period_start_utc, resolution_minutes
        order by _loaded_at desc, document_created_at desc
    ) = 1
)

select
    bidding_zone_eic,
    psr_type,
    flow_direction,
    period_start_utc,
    period_start_utc + resolution_minutes * interval 1 minute as period_end_utc,
    resolution_minutes,
    quantity_mw,
    document_mrid,
    document_created_at,
    _loaded_at as loaded_at
from latest
