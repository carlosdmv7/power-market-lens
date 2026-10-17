-- Hourly generation per bidding zone and production type, with local
-- delivery date and hour. Grain: zone × UTC hour × production type.

with hourly as (
    select * from {{ ref('int_generation__aggregated_hourly') }}
),

zones as (
    select * from {{ ref('bidding_zones') }}
),

psr_types as (
    select * from {{ ref('entsoe_psr_types') }}
)

select
    hourly.bidding_zone_eic,
    zones.zone_code,
    hourly.hour_start_utc,
    {{ local_time_columns('hourly.hour_start_utc', 'zones.timezone') }},
    hourly.psr_type,
    psr_types.psr_name,
    hourly.generation_mw,
    hourly.consumption_mw,
    hourly.resolution_minutes,
    hourly.n_generation_intervals,
    hourly.loaded_at
from hourly
left join zones
    on hourly.bidding_zone_eic = zones.bidding_zone_eic
left join psr_types
    on hourly.psr_type = psr_types.psr_type
