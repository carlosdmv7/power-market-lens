-- Each price interval checked against the SDAC harmonised limits in force on
-- its delivery day (ADR-003). Out-of-range prices are flagged, never dropped.
-- The delivery day is taken in market time (CET/CEST) for every zone, because
-- that is the clock the limits and SDAC itself run on.

with prices as (
    select * from {{ ref('stg_entsoe__day_ahead_prices') }}
),

limits as (
    select * from {{ ref('sdac_price_limits') }}
),

dated as (
    select
        *,
        cast(
            (period_start_utc at time zone '{{ var("sdac_market_timezone") }}') as date
        ) as market_date
    from prices
)

select
    dated.bidding_zone_eic,
    dated.period_start_utc,
    dated.period_end_utc,
    dated.resolution_minutes,
    dated.market_date,
    dated.price_eur_mwh,
    limits.min_price_eur_mwh,
    limits.max_price_eur_mwh,
    dated.price_eur_mwh not between limits.min_price_eur_mwh
    and limits.max_price_eur_mwh as is_price_out_of_range,
    dated.loaded_at
from dated
left join limits
    on
        dated.market_date >= limits.valid_from
        and (limits.valid_to is null or dated.market_date < limits.valid_to)
