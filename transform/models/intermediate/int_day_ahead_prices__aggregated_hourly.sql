-- Day-ahead prices rolled up to the UTC hour (ADR-003).
-- Since 2025-10-01 an hour is four 15-minute prices; the hourly price is their
-- arithmetic mean. If an hour was published at more than one resolution, only
-- the finest is used, so a 60-minute series never dilutes the quarter-hours.

with validated as (
    select * from {{ ref('int_day_ahead_prices__validated') }}
),

bucketed as (
    select
        *,
        time_bucket(interval 1 hour, period_start_utc) as hour_start_utc
    from validated
),

finest as (
    select *
    from bucketed
    qualify resolution_minutes = min(resolution_minutes) over (
        partition by bidding_zone_eic, hour_start_utc
    )
)

select
    bidding_zone_eic,
    hour_start_utc,
    any_value(resolution_minutes) as resolution_minutes,
    count(*) as n_intervals,
    count(*) * any_value(resolution_minutes) = 60 as is_complete_hour,
    avg(price_eur_mwh) as price_eur_mwh,
    min(price_eur_mwh) as min_interval_price_eur_mwh,
    max(price_eur_mwh) as max_interval_price_eur_mwh,
    bool_or(is_price_out_of_range) as has_price_out_of_range,
    max(loaded_at) as loaded_at
from finest
group by bidding_zone_eic, hour_start_utc
