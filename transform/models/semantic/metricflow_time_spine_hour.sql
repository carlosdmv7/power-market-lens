-- One row per UTC hour: the time spine MetricFlow joins metrics to, at the
-- marts' own grain. Starts with the data (January 2024) and runs past today, so
-- a metric asked for by day, week or month always finds its periods.
-- Grain: UTC hour.

select cast(range as timestamptz) as hour_start_utc
from range(
    timestamptz '2024-01-01 00:00:00+00',
    timestamptz '2030-01-01 00:00:00+00',
    interval 1 hour
)
