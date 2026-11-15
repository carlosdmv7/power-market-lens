-- What a household pays per kWh, per country, half-year and consumption band,
-- and how much of it is taxes. Eurostat publishes the price three times over
-- (without taxes, without VAT, with everything); the differences between
-- them are the taxes and levies, and the VAT. Grain: country × half-year × band.

with prices as (
    select * from {{ ref('stg_eurostat__household_prices') }}
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

pivoted as (
    select
        country_code,
        period_code,
        period_start_date,
        nrg_cons,
        max(price_eur_kwh) filter (where tax = 'X_TAX') as price_excl_taxes_eur_kwh,
        max(price_eur_kwh) filter (where tax = 'X_VAT') as price_excl_vat_eur_kwh,
        max(price_eur_kwh) filter (where tax = 'I_TAX') as price_incl_all_taxes_eur_kwh,
        string_agg(distinct status_flag, ',' order by status_flag) as status_flags,
        max(source_updated_at) as source_updated_at
    from prices
    group by country_code, period_code, period_start_date, nrg_cons
)

select
    pivoted.country_code,
    countries.country_name,
    pivoted.period_code,
    pivoted.period_start_date,
    bands.band_code,
    bands.band_label,
    pivoted.price_excl_taxes_eur_kwh,
    -- Can be negative: in the Netherlands a fixed energy-tax refund per
    -- connection exceeds what small consumers pay in energy tax.
    pivoted.price_excl_vat_eur_kwh - pivoted.price_excl_taxes_eur_kwh as taxes_and_levies_eur_kwh,
    pivoted.price_incl_all_taxes_eur_kwh - pivoted.price_excl_vat_eur_kwh as vat_eur_kwh,
    pivoted.price_incl_all_taxes_eur_kwh,
    pivoted.status_flags,
    pivoted.source_updated_at
from pivoted
-- Inner join: the pre-2007 bands (KWH600, KWH3500...) are not in the seed
-- and are left out on purpose; Eurostat has them for 2007 only.
inner join bands
    on pivoted.nrg_cons = bands.nrg_cons
left join countries
    on pivoted.country_code = countries.country_code
