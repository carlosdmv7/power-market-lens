-- What a household's price per kWh is made of, per country, year and
-- consumption band: energy and supply, network costs, taxes and levies, VAT.
-- Eurostat's yearly component dataset, not the half-yearly prices: the two
-- are compiled differently and do not add up to each other (ADR-010), so they
-- are never mixed in one figure. Grain: country × year × band × component.

with components as (
    select * from {{ ref('stg_eurostat__household_price_components') }}
),

catalogue as (
    select * from {{ ref('eurostat_price_components') }}
),

bands as (
    select * from {{ ref('eurostat_consumption_bands') }}
),

countries as (
    select distinct
        country_code,
        country_name
    from {{ ref('bidding_zones') }}
),

top_level as (
    -- The four top-level components. Their sub-items (renewable, capacity,
    -- environmental taxes...) do not add up to the taxes total in Eurostat's
    -- data, so they stay in staging rather than being summed here.
    select
        components.*,
        catalogue.component_group,
        catalogue.component_label
    from components
    inner join catalogue
        on components.nrg_prc = catalogue.nrg_prc
    where catalogue.is_top_level
)

select
    top_level.country_code,
    countries.country_name,
    top_level.year,
    bands.band_code,
    bands.band_label,
    top_level.component_group as component,
    top_level.component_label,
    top_level.component_eur_kwh,
    top_level.component_eur_kwh / sum(top_level.component_eur_kwh) over (
        partition by top_level.country_code, top_level.year, bands.band_code
    ) as share_of_total,
    top_level.status_flag,
    top_level.source_updated_at
from top_level
inner join bands
    on top_level.nrg_cons = bands.nrg_cons
left join countries
    on top_level.country_code = countries.country_code
