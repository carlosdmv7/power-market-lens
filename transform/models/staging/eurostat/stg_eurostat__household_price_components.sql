-- Household electricity price components, the latest version Eurostat has
-- published. Same rule as stg_eurostat__household_prices (ADR-010).

with source as (
    select * from {{ source('eurostat', 'household_price_components') }}
),

latest_version as (
    select *
    from source
    qualify source_updated_at = max(source_updated_at) over ()
),

deduplicated as (
    select *
    from latest_version
    qualify row_number() over (
        partition by geo, time_period, nrg_cons, nrg_prc
        order by _loaded_at desc
    ) = 1
)

select
    geo as country_code,
    cast(time_period as integer) as year,  -- noqa: RF04
    nrg_cons,
    nrg_prc,
    currency,
    component_eur_kwh,
    status_flag,
    source_updated_at,
    _loaded_at as loaded_at
from deduplicated
