{#
    Local delivery-time columns for an hourly UTC timestamp (ADR-003).

    `hour_column` is a timestamptz; `timezone_column` holds an IANA zone name.
    `timestamptz AT TIME ZONE tz` gives the local wall-clock time; a naive
    `timestamp AT TIME ZONE tz` goes the other way, reading it as local time.
    Every number here comes from epoch differences between instants, so the
    session time zone can never leak into the result.

    - delivery_hour_local repeats 2 on the October change and skips it in March,
      so local columns are attributes, never part of a key.
    - hours_in_delivery_day is 23, 24 or 25: the completeness tests expect it.
#}
{% macro local_time_columns(hour_column, timezone_column) -%}
    cast(({{ hour_column }} at time zone {{ timezone_column }}) as date) as delivery_date_local,
    hour({{ hour_column }} at time zone {{ timezone_column }}) as delivery_hour_local,
    cast(
        (
            epoch({{ hour_column }} at time zone {{ timezone_column }})
            - epoch({{ hour_column }} at time zone 'UTC')
        ) / 60 as integer
    ) as utc_offset_minutes,
    cast(
        (
            epoch(
                cast(cast(({{ hour_column }} at time zone {{ timezone_column }}) as date) + 1 as timestamp)
                at time zone {{ timezone_column }}
            )
            - epoch(
                cast(cast(({{ hour_column }} at time zone {{ timezone_column }}) as date) as timestamp)
                at time zone {{ timezone_column }}
            )
        ) / 3600 as integer
    ) as hours_in_delivery_day
{%- endmacro %}
