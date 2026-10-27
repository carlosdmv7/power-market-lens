-- Total load rolled up to the UTC hour, actual and forecast side by side.
-- Load is average power (MW), so the hourly value is the mean of the
-- intervals. If an hour was published at more than one resolution, only the
-- finest is used, as for prices and generation.

with total_load as (
    select * from {{ ref('stg_entsoe__total_load') }}
),

bucketed as (
    select
        *,
        time_bucket(interval 1 hour, period_start_utc) as hour_start_utc
    from total_load
),

finest as (
    select *
    from bucketed
    qualify resolution_minutes = min(resolution_minutes) over (
        partition by bidding_zone_eic, load_kind, hour_start_utc
    )
)

select
    bidding_zone_eic,
    hour_start_utc,
    avg(load_mw) filter (where load_kind = 'actual') as actual_load_mw,
    avg(load_mw) filter (where load_kind = 'day_ahead_forecast') as forecast_load_mw,
    count(*) filter (where load_kind = 'actual') as n_actual_intervals,
    min(resolution_minutes) as resolution_minutes,
    max(loaded_at) as loaded_at
from finest
group by bidding_zone_eic, hour_start_utc
