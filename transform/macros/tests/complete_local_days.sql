{#
    Generic test: every local delivery day has all its hours (ADR-003).

    For each partition (a bidding zone), every day strictly between the first
    and the last loaded day must exist and hold exactly `hours_in_delivery_day`
    distinct hours: 23, 24 or 25. The first and last days are skipped because a
    load window rarely starts and ends on local midnight. Days listed for this
    model in the known_source_gaps seed (data the publisher never released) are
    skipped; any other incomplete day still fails. Returns the failing days
    with the hours found and expected.

    `older_than_days` and `within_days` restrict the test to days at least, or
    less than, that many days before today (UTC). Measured data can be
    published late, and every run re-pulls the trailing window, so a model can
    carry the test twice: failing on days the window no longer reaches, and
    only warning on days it still does (ADR-006, amendment). With neither
    argument the test covers every day.
#}
{% test complete_local_days(
    model, partition_by, hour_column='hour_start_utc', older_than_days=none, within_days=none
) %}

with hours_per_day as (
    select
        {{ partition_by }} as partition_key,
        delivery_date_local,
        count(distinct {{ hour_column }}) as n_hours,
        any_value(hours_in_delivery_day) as expected_hours
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
),

known_gaps as (
    select
        bidding_zone_eic as partition_key,
        delivery_date_local
    from {{ ref('known_source_gaps') }}
    where model_name = '{{ model.identifier }}'
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
left join known_gaps
    on
        calendar.partition_key = known_gaps.partition_key
        and calendar.delivery_date_local = known_gaps.delivery_date_local
where
    (hours_per_day.n_hours is null or hours_per_day.n_hours != hours_per_day.expected_hours)
    and known_gaps.partition_key is null
    {% if older_than_days is not none %}
    and calendar.delivery_date_local < current_date - {{ older_than_days }}
    {% endif %}
    {% if within_days is not none %}
    and calendar.delivery_date_local >= current_date - {{ within_days }}
    {% endif %}

{% endtest %}
