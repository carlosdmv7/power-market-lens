"""Row models for the raw tables, one per source entity.

Each parsed point is validated here before it can reach the warehouse: a naive
timestamp, an unknown resolution or a missing value fails at ingestion instead
of surfacing later as a silently wrong hour in a mart. The fields mirror the
raw table columns except the load metadata (`_loaded_at`, `_source`,
`_window_*`, `_run_id`), which the loader adds per batch.
"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

# Market time units ENTSO-E publishes for these documents. SDAC moved from 60
# to 15 minutes on 2025-10-01 (ADR-003); 30 appears in a few zones.
ResolutionMinutes = Literal[15, 30, 60]


class _Row(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DayAheadPriceRow(_Row):
    """One day-ahead price point (document A44)."""

    bidding_zone_eic: str
    period_start_utc: AwareDatetime
    resolution_minutes: ResolutionMinutes
    price_eur_mwh: float
    currency: str
    price_unit: str
    document_mrid: str
    document_created_at: AwareDatetime


class GenerationRow(_Row):
    """One actual generation point for a production type (document A75)."""

    bidding_zone_eic: str
    psr_type: str
    # Pumped storage reports both: generation into the zone and consumption out
    # of it (pumping).
    flow_direction: Literal["generation", "consumption"]
    period_start_utc: AwareDatetime
    resolution_minutes: ResolutionMinutes
    quantity_mw: float
    document_mrid: str
    document_created_at: AwareDatetime


class TotalLoadRow(_Row):
    """One total load point (document A65): actual (A16) or day-ahead forecast (A01)."""

    bidding_zone_eic: str
    # ENTSO-E process type: A16 realised, A01 day-ahead forecast.
    process_type: Literal["A16", "A01"]
    period_start_utc: AwareDatetime
    resolution_minutes: ResolutionMinutes
    load_mw: float
    document_mrid: str
    document_created_at: AwareDatetime


class CrossBorderFlowRow(_Row):
    """One physical flow point from one zone into another (document A11).

    ENTSO-E publishes each direction as its own non-negative series, so a
    border is two series: out→in and in→out.
    """

    out_zone_eic: str
    in_zone_eic: str
    period_start_utc: AwareDatetime
    resolution_minutes: ResolutionMinutes
    flow_mw: float
    document_mrid: str
    document_created_at: AwareDatetime


class HouseholdPriceRow(_Row):
    """One Eurostat household electricity price (dataset nrg_pc_204), as published.

    Half-yearly, per country, consumption band and tax treatment.
    """

    geo: str  # Eurostat country code, e.g. ES, DE
    time_period: str  # half-year as Eurostat writes it, e.g. 2025-S2
    nrg_cons: str  # consumption band, e.g. KWH2500-4999 (band DC)
    tax: Literal["X_TAX", "X_VAT", "I_TAX"]  # excluding taxes, excluding VAT, all included
    currency: Literal["EUR"]
    price_eur_kwh: float
    status_flag: str | None  # Eurostat flag: p provisional, d definition differs, ...
    source_updated_at: AwareDatetime  # when Eurostat last updated the dataset


class HouseholdPriceComponentRow(_Row):
    """One Eurostat household price component (dataset nrg_pc_204_c), as published.

    Yearly, per country, consumption band and component (energy and supply,
    network costs, taxes and levies, VAT, and their sub-items and allowances).
    """

    geo: str
    time_period: str  # year, e.g. 2025
    nrg_cons: str
    nrg_prc: str  # price component, e.g. NRG_SUP, NETC, TAX_FEE_LEV_CHRG, VAT
    currency: Literal["EUR"]
    component_eur_kwh: float
    status_flag: str | None
    source_updated_at: AwareDatetime
