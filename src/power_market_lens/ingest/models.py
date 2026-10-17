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
