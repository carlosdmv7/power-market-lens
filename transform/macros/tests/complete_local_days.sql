{#
    Generic test: every local delivery day has all its intervals (ADR-003).

    For each partition (a bidding zone), every day strictly between the first
    and the last loaded day must exist and hold exactly `hours_in_delivery_day`
    × `intervals_per_hour` distinct intervals: 23, 24 or 25 hours, or 92, 96
    or 100 quarter-hours with `intervals_per_hour=4`. The first and last days
    are skipped because a load window rarely starts and ends on local midnight.
    Returns the failing days with the intervals found and expected.
#}
{% test complete_local_days(model, partition_by, hour_column='hour_start_utc', intervals_per_hour=1) %}

with hours_per_day as (
    select
        {{ partition_by }} as partition_key,
        delivery_date_local,
        count(distinct {{ hour_column }}) as n_hours,
        any_value(hours_in_delivery_day) * {{ intervals_per_hour }} as expected_hours
    from {{ model }}
    group by 1, 2
),

bounds as (
    select
        partition_key,
        min(delivery_date_local) as first_day,
        max(delivery_date_local) as last_day
    from hours_per_day
    group by 1
),

calendar as (
    select
        partition_key,
        cast(
            unnest(
                generate_series(
                    cast(first_day + 1 as timestamp),
                    cast(last_day - 1 as timestamp),
                    interval 1 day
                )
            ) as date
        ) as delivery_date_local
    from bounds
    where last_day - first_day >= 2
)

select
    calendar.partition_key,
    calendar.delivery_date_local,
    coalesce(hours_per_day.n_hours, 0) as n_hours,
    hours_per_day.expected_hours
from calendar
left join hours_per_day
    on
        calendar.partition_key = hours_per_day.partition_key
        and calendar.delivery_date_local = hours_per_day.delivery_date_local
where hours_per_day.n_hours is null or hours_per_day.n_hours != hours_per_day.expected_hours

{% endtest %}
