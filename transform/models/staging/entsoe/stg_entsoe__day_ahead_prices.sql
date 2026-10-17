-- One row per published price interval: the latest version of each.
-- Raw holds a copy per load (append-only, ADR-003); the newest load wins, and
-- within one load the newest ENTSO-E document. Resolution is part of the key,
-- so a zone publishing both 15- and 60-minute series keeps both here.

with source as (
    select * from {{ source('entsoe', 'day_ahead_prices') }}
),

latest as (
    select *
    from source
    qualify row_number() over (
        partition by bidding_zone_eic, period_start_utc, resolution_minutes
        order by _loaded_at desc, document_created_at desc
    ) = 1
)

select
    bidding_zone_eic,
    period_start_utc,
    period_start_utc + resolution_minutes * interval 1 minute as period_end_utc,
    resolution_minutes,
    price_eur_mwh,
    currency,
    price_unit,
    document_mrid,
    document_created_at,
    _loaded_at as loaded_at
from latest
