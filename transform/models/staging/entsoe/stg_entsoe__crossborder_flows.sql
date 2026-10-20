-- One row per direction of a border and interval: the latest version.
-- Same deduplication rule as the prices (ADR-003).

with source as (
    select * from {{ source('entsoe', 'crossborder_flows') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by out_zone_eic, in_zone_eic, period_start_utc, resolution_minutes
        order by _loaded_at desc, document_created_at desc
    ) = 1
)

select
    out_zone_eic,
    in_zone_eic,
    period_start_utc,
    period_start_utc + resolution_minutes * interval 1 minute as period_end_utc,
    resolution_minutes,
    flow_mw,
    document_mrid,
    document_created_at,
    _loaded_at as loaded_at
from latest
