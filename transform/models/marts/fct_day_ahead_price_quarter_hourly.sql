-- Day-ahead price at the market's native 15-minute resolution, with the
-- zone's local delivery date, hour and quarter. Grain: zone × UTC quarter-hour.
-- Only quarter-hour series are kept (SDAC since 2025-10-01): earlier hourly
-- prices live in fct_day_ahead_price_hourly and are not split into quarters.

with validated as (
    select * from {{ ref('int_day_ahead_prices__validated') }}
    where resolution_minutes = 15
),

zones as (
    select * from {{ ref('bidding_zones') }}
)

select
    validated.bidding_zone_eic,
    zones.zone_code,
    validated.period_start_utc,
    validated.period_end_utc,
    -- The hour the quarter belongs to: the join key to the hourly marts.
    time_bucket(interval 1 hour, validated.period_start_utc) as hour_start_utc,
    {{ local_time_columns('validated.period_start_utc', 'zones.timezone') }},
    cast(
        minute(validated.period_start_utc at time zone zones.timezone) / 15 + 1 as integer
    ) as quarter_of_hour,
    validated.price_eur_mwh,
    validated.is_price_out_of_range,
    validated.loaded_at
from validated
-- Left join: a zone missing from the seed shows up as a null zone_code and
-- fails a test, instead of silently disappearing.
left join zones
    on validated.bidding_zone_eic = zones.bidding_zone_eic
