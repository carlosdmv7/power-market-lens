-- Hourly interchange of each modelled zone with each zone it borders:
-- imports, exports and the net, seen from the modelled zone, with its local
-- delivery date and hour. Grain: zone × counterpart × UTC hour.
-- A border between two modelled zones (ES–PT, the Italian zones) appears
-- once from each side; a neighbour outside the model only as a counterpart.

with hourly as (
    select * from {{ ref('int_crossborder_flows__aggregated_hourly') }}
),

zones as (
    select * from {{ ref('bidding_zones') }}
),

counterparts as (
    select
        bidding_zone_eic,
        zone_code,
        true as is_modelled
    from {{ ref('bidding_zones') }}
    union all
    select
        bidding_zone_eic,
        zone_code,
        false as is_modelled
    from {{ ref('neighbour_zones') }}
),

-- Each direction counts twice: an import of the zone it enters and an export
-- of the zone it leaves.
sides as (
    select
        in_zone_eic as bidding_zone_eic,
        out_zone_eic as counterpart_eic,
        hour_start_utc,
        flow_mw as import_mw,
        cast(null as double) as export_mw,
        is_complete_hour,
        loaded_at
    from hourly
    union all
    select
        out_zone_eic as bidding_zone_eic,
        in_zone_eic as counterpart_eic,
        hour_start_utc,
        cast(null as double) as import_mw,
        flow_mw as export_mw,
        is_complete_hour,
        loaded_at
    from hourly
),

paired as (
    -- At most one import row and one export row per zone, counterpart and
    -- hour (the intermediate grain), so max() just picks each.
    select
        bidding_zone_eic,
        counterpart_eic,
        hour_start_utc,
        max(import_mw) as import_mw,
        max(export_mw) as export_mw,
        count(*) = 2 and bool_and(is_complete_hour) as is_complete_hour,
        max(loaded_at) as loaded_at
    from sides
    group by bidding_zone_eic, counterpart_eic, hour_start_utc
)

select
    paired.bidding_zone_eic,
    zones.zone_code,
    paired.counterpart_eic,
    counterparts.zone_code as counterpart_code,
    counterparts.is_modelled as is_counterpart_modelled,
    paired.hour_start_utc,
    {{ local_time_columns('paired.hour_start_utc', 'zones.timezone') }},
    paired.import_mw,
    paired.export_mw,
    -- Null when a direction is missing for the hour: a net from one side
    -- alone would pass for a full import or export.
    paired.import_mw - paired.export_mw as net_import_mw,
    paired.is_complete_hour,
    paired.loaded_at
from paired
-- Inner join: only modelled zones get a point of view. A modelled zone
-- missing from the seed would vanish here, so a Python test keeps the seed
-- equal to zones.ZONES.
inner join zones
    on paired.bidding_zone_eic = zones.bidding_zone_eic
-- Left join: a counterpart missing from both seeds shows up as a null
-- counterpart_code and fails a test.
left join counterparts
    on paired.counterpart_eic = counterparts.bidding_zone_eic
