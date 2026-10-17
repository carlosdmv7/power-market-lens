-- Generation per production type rolled up to the UTC hour.
-- Quantities are average power (MW), so the hourly value is the mean of the
-- intervals: over a complete hour it equals the energy produced, in MWh.
-- Generation and consumption (pumping) become two columns of one row.

with generation as (
    select * from {{ ref('stg_entsoe__generation_per_type') }}
),

bucketed as (
    select
        *,
        time_bucket(interval 1 hour, period_start_utc) as hour_start_utc
    from generation
),

finest as (
    select *
    from bucketed
    qualify resolution_minutes = min(resolution_minutes) over (
        partition by bidding_zone_eic, psr_type, flow_direction, hour_start_utc
    )
)

select
    bidding_zone_eic,
    psr_type,
    hour_start_utc,
    min(resolution_minutes) as resolution_minutes,
    avg(quantity_mw) filter (where flow_direction = 'generation') as generation_mw,
    avg(quantity_mw) filter (where flow_direction = 'consumption') as consumption_mw,
    count(*) filter (where flow_direction = 'generation') as n_generation_intervals,
    max(loaded_at) as loaded_at
from finest
group by bidding_zone_eic, psr_type, hour_start_utc
