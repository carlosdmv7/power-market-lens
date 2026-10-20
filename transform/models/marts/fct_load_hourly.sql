-- Hourly total load per bidding zone: actual and the day-ahead forecast, with
-- the zone's local delivery date and hour. Grain: zone × UTC hour. Hours
-- still ahead carry the forecast only.

with hourly as (
    select * from {{ ref('int_total_load__aggregated_hourly') }}
),

zones as (
    select * from {{ ref('bidding_zones') }}
)

select
    hourly.bidding_zone_eic,
    zones.zone_code,
    hourly.hour_start_utc,
    {{ local_time_columns('hourly.hour_start_utc', 'zones.timezone') }},
    hourly.actual_load_mw,
    hourly.forecast_load_mw,
    -- Positive: the TSO expected more load than there was.
    hourly.forecast_load_mw - hourly.actual_load_mw as forecast_error_mw,
    hourly.resolution_minutes,
    hourly.n_actual_intervals,
    hourly.loaded_at
from hourly
-- Left join: a zone missing from the seed shows up as a null zone_code and
-- fails a test, instead of silently disappearing.
left join zones
    on hourly.bidding_zone_eic = zones.bidding_zone_eic
