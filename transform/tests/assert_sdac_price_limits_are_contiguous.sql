-- The limit periods must tile time with no gap and no overlap: each period
-- ends exactly where the next one starts, only the last one is open-ended,
-- and every floor sits below its cap. A gap would leave some delivery days
-- with no limits to check prices against; an overlap would give them two.
-- Returns the offending rows; the test passes when it returns none.

with limits as (
    select
        valid_from,
        valid_to,
        min_price_eur_mwh,
        max_price_eur_mwh,
        lead(valid_from) over (order by valid_from) as next_valid_from
    from {{ ref('sdac_price_limits') }}
)

select *
from limits
where
    min_price_eur_mwh >= max_price_eur_mwh
    or (next_valid_from is not null and valid_to is distinct from next_valid_from)
    or (next_valid_from is null and valid_to is not null)
