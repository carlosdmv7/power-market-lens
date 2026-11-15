-- Physical flows rolled up to the UTC hour, per direction of a border.
-- Flow is average power (MW), so the hourly value is the mean of the
-- intervals. If an hour was published at more than one resolution, only the
-- finest is used, as for prices, generation and load.

with flows as (
    select * from {{ ref('stg_entsoe__crossborder_flows') }}
),

bucketed as (
    select
        *,
        time_bucket(interval 1 hour, period_start_utc) as hour_start_utc
    from flows
),

finest as (
    select *
    from bucketed
    qualify resolution_minutes = min(resolution_minutes) over (
        partition by out_zone_eic, in_zone_eic, hour_start_utc
    )
)

select
    out_zone_eic,
    in_zone_eic,
    hour_start_utc,
    any_value(resolution_minutes) as resolution_minutes,
    count(*) as n_intervals,
    count(*) * any_value(resolution_minutes) = 60 as is_complete_hour,
    avg(flow_mw) as flow_mw,
    max(loaded_at) as loaded_at
from finest
group by out_zone_eic, in_zone_eic, hour_start_utc
