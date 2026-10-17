-- Hourly day-ahead price per bidding zone, with the zone's local delivery
-- date and hour. Grain: zone × UTC hour. This is what the app reads.

with hourly as (
    select * from {{ ref('int_day_ahead_prices__aggregated_hourly') }}
),

zones as (
    select * from {{ ref('bidding_zones') }}
)

select
    hourly.bidding_zone_eic,
    zones.zone_code,
    hourly.hour_start_utc,
    {{ local_time_columns('hourly.hour_start_utc', 'zones.timezone') }},
    hourly.price_eur_mwh,
    hourly.min_interval_price_eur_mwh,
    hourly.max_interval_price_eur_mwh,
    hourly.resolution_minutes,
    hourly.n_intervals,
    hourly.is_complete_hour,
    hourly.has_price_out_of_range,
    hourly.loaded_at
from hourly
-- Left join: a zone missing from the seed shows up as a null zone_code and
-- fails a test, instead of silently disappearing.
left join zones
    on hourly.bidding_zone_eic = zones.bidding_zone_eic
