-- Household electricity prices, the latest version Eurostat has published
-- (ADR-010). Raw holds one full copy of the dataset per version. A value that
-- Eurostat withdraws in a later version must not survive from an older one,
-- so the whole newest version is kept, not the newest row per key.

with source as (
    select * from {{ source('eurostat', 'household_prices') }}
),

latest_version as (
    select *
    from source
    qualify source_updated_at = max(source_updated_at) over ()
),

deduplicated as (
    -- The same version loaded twice (a retried load) keeps one copy.
    select *
    from latest_version
    qualify row_number() over (
        partition by geo, time_period, nrg_cons, tax
        order by _loaded_at desc
    ) = 1
)

select
    geo as country_code,
    time_period as period_code,
    make_date(
        cast(left(time_period, 4) as integer),
        case right(time_period, 2) when 'S1' then 1 else 7 end,
        1
    ) as period_start_date,
    nrg_cons,
    tax,
    currency,
    price_eur_kwh,
    status_flag,
    source_updated_at,
    _loaded_at as loaded_at
from deduplicated
