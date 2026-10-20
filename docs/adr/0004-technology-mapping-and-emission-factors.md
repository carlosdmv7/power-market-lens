# ADR-004 — Technology mapping and emission factors

- **Status:** proposed (awaiting review)
- **Date:** 2026-10-20

## Context

Phase 1 reports, per bidding zone and hour, the renewable share, the
low-carbon share and a production-based carbon intensity. ENTSO-E reports
generation in 21 production types (PSR types B01–B20, B25), which say nothing
about emissions or about which sources count as renewable.

The emission factors were decided on 2026-10-19: IPCC AR5 lifecycle medians.
They come from Working Group III, Annex III, Table A.III.2 (2014), "Emissions
of selected electricity supply technologies", copied as printed from the
official PDF. These are **lifecycle** values, including the albedo effect.
The direct-emission medians in the same table are lower (coal 760, gas 370),
and sources that quote those are not wrong, only answering another question.

AR5 has thirteen rows for the technologies in use. Ten PSR types have no row
of their own. On a week of real data for the twelve zones they are about 7%
of generation:

| PSR type | Share of generation | Treatment proposed |
|---|---|---|
| B10 pumped storage | 3.2% | storage, set aside (see 3) |
| B02 lignite | 2.0% | proxy: Coal — PC, 820 |
| B17 waste | 0.8% | proxy: Biomass — dedicated, 230 |
| B20 other | 0.5% | proxy: Gas — Combined Cycle, 490 |
| B15 other renewable | 0.2% | proxy: Biomass — dedicated, 230 |
| B06 oil | 0.1% | proxy: Coal — PC, 820 |
| B03 coal-derived gas | 0.1% | proxy: Coal — PC, 820 |
| B25 energy storage | 0.1% | storage, set aside |
| B07 oil shale, B08 peat | 0.0% | proxy: Coal — PC, 820 |

## Decision

1. **One seed classifies every PSR type** (`technology_mapping`): technology
   name, the app's display group, renewable, low-carbon (renewable or
   nuclear), storage, and which AR5 row gives its factor. A test makes every
   ENTSO-E code appear in it.
2. **One seed holds the AR5 table** (`emission_factors_ipcc_ar5`): minimum,
   median and maximum per technology, as printed. The marts use the median.
3. **Storage is not production.** Pumped hydro and batteries re-deliver energy
   generated earlier; counting their discharge with a factor of its own would
   count that energy twice. It is left out of production, shares and
   intensity, and reported as `storage_discharge_mw`.
4. **A type without an AR5 row takes a stated proxy and is flagged**
   (`is_proxy_factor`). Every fossil type without its own row uses Coal — PC.
   Waste and "other renewable" use Biomass — dedicated. "Other", mostly
   gas-fired cogeneration in Spain and Italy, uses Gas — Combined Cycle.
5. **Every intensity says how much of it is proxy.** `fct_generation_mix_hourly`
   carries `proxy_factor_share`, so the app can qualify a number instead of
   presenting it as clean.
6. **Classification choices:** waste is not renewable (only part of it is
   biogenic); pumped storage is neither renewable nor low-carbon; nuclear is
   low-carbon, not renewable.
7. **No factor, no silence:** generation whose type has no factor would lower
   the intensity without a trace, so the mart carries `unfactored_mw` and a
   test keeps it at zero.

## Consequences

- **Germany is understated.** Lignite emits more than hard coal, and AR5 has
  no lignite row, so 23% of DE-LU's production takes the coal proxy. On the
  first week of data DE-LU averages 325 gCO2eq/kWh; its true lifecycle figure
  is higher. Options, for review:
  - (a) keep AR5 only and show the proxy share (as built);
  - (b) add a lignite factor from a second source, dated and cited in the
    seed, which ends the "AR5 only" rule;
  - (c) show DE-LU's intensity as a range, using AR5's coal maximum (910).
- **Open-cycle gas** emits more than the combined-cycle median, so gas-heavy
  peaking hours are also understated. AR5 has no open-cycle row.
- **Solar** uses utility-scale PV (48); rooftop is 41. ENTSO-E does not split
  the two.
- **Factors are global medians**, not plant- or country-specific. The
  intensity is a consistent comparison across zones, not a regulatory figure.
- The app's five display groups (`display_group`) replace the grouping in
  `app/mix.py`.

## Alternatives considered

- **Direct emissions only** (renewables and nuclear zero). Simpler, but it
  undercounts fossil generation against lifecycle studies and makes zones
  less comparable. Declined on 2026-10-19.
- **Consumption-based intensity** (following imports through the flows, as
  Electricity Maps does). More informative for a household, but it needs the
  cross-border flows first. A later phase can add it on top of these factors.
