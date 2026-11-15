-- One row per bidding zone, with the names the app shows. A straight copy of
-- the bidding_zones seed: the app reads marts only, never seeds.

with zones as (
    select * from {{ ref('bidding_zones') }}
)

select
    bidding_zone_eic,
    zone_code,
    zone_name,
    country_code,
    country_name,
    timezone
from zones
