-- Production-based renewable share, low-carbon share and carbon intensity per
-- zone and hour (ADR-004). Storage re-delivers energy produced earlier, so it
-- is left out of production and reported on its own. Grain: zone × UTC hour.

with generation as (
    select * from {{ ref('fct_generation_hourly') }}
    where generation_mw is not null
),

technologies as (
    select * from {{ ref('technology_mapping') }}
),

factors as (
    select * from {{ ref('emission_factors_ipcc_ar5') }}
),

typed as (
    select
        generation.*,
        technologies.is_renewable,
        technologies.is_low_carbon,
        technologies.is_storage,
        technologies.is_proxy_factor,
        factors.lifecycle_median_g_per_kwh
    from generation
    left join technologies
        on generation.psr_type = technologies.psr_type
    left join factors
        on technologies.factor_technology = factors.technology
),

hourly as (
    select
        bidding_zone_eic,
        zone_code,
        hour_start_utc,
        any_value(delivery_date_local) as delivery_date_local,
        any_value(delivery_hour_local) as delivery_hour_local,
        any_value(utc_offset_minutes) as utc_offset_minutes,
        any_value(hours_in_delivery_day) as hours_in_delivery_day,
        sum(generation_mw) filter (where not is_storage) as production_mw,
        sum(generation_mw) filter (where not is_storage and is_renewable) as renewable_mw,
        sum(generation_mw) filter (where not is_storage and is_low_carbon) as low_carbon_mw,
        sum(generation_mw) filter (where not is_storage and is_proxy_factor) as proxy_factor_mw,
        -- MW × g/kWh summed, then divided by MW: the generation-weighted
        -- factor of the hour, in g/kWh.
        sum(generation_mw * lifecycle_median_g_per_kwh) filter (
            where not is_storage
        ) as weighted_emissions,
        -- Generation with no factor would silently lower the intensity: a
        -- test keeps this at zero.
        coalesce(sum(generation_mw) filter (
            where not is_storage and lifecycle_median_g_per_kwh is null
        ), 0) as unfactored_mw,
        coalesce(sum(generation_mw) filter (where is_storage), 0) as storage_discharge_mw,
        max(loaded_at) as loaded_at
    from typed
    group by bidding_zone_eic, zone_code, hour_start_utc
)

select
    bidding_zone_eic,
    zone_code,
    hour_start_utc,
    delivery_date_local,
    delivery_hour_local,
    utc_offset_minutes,
    hours_in_delivery_day,
    production_mw,
    -- A filtered sum over no rows is null, not zero: an hour without
    -- renewables has a renewable share of 0, not an unknown one.
    coalesce(renewable_mw, 0) / nullif(production_mw, 0) as renewable_share,
    coalesce(low_carbon_mw, 0) / nullif(production_mw, 0) as low_carbon_share,
    weighted_emissions / nullif(production_mw, 0) as carbon_intensity_g_per_kwh,
    coalesce(proxy_factor_mw, 0) / nullif(production_mw, 0) as proxy_factor_share,
    storage_discharge_mw,
    unfactored_mw,
    loaded_at
from hourly
