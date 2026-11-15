"""Eurostat household electricity prices: fetch, parse and append to raw.

Two datasets, both per country and consumption band, in euros (ADR-010):

- ``nrg_pc_204``: the price, half-yearly, excluding taxes, excluding VAT, and
  with every tax included;
- ``nrg_pc_204_c``: the same price split into components, yearly: energy and
  supply, network costs, taxes, fees, levies and charges, VAT, and their
  sub-items and allowances.

The API answers in JSON-stat: one flat list of values plus, per dimension,
the position of each category. ``observations`` turns that back into one
record per value. Eurostat flags values ("p" provisional, "d" definition
differs...); the flag is kept next to the value, never dropped.

A dataset changes a few times a year, so a load appends only when Eurostat's
``updated`` time is newer than the newest one already in raw. Raw stays
append-only and keeps every published version; it just does not store the
same version every day.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
import time

import duckdb
import requests

from power_market_lens.ingest.models import HouseholdPriceComponentRow, HouseholdPriceRow
from power_market_lens.ingest.warehouse import RAW_SCHEMA, append_rows

log = logging.getLogger(__name__)

API = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
# The countries of the modelled bidding zones (the country_code column of the
# bidding_zones seed; a test keeps them equal). DE-LU is priced as Germany.
COUNTRIES = ("DE", "ES", "FR", "IT", "NL", "PT")


class EurostatDocumentError(ValueError):
    """The response is not the dataset asked for, or has an unexpected shape."""


@dataclass(frozen=True)
class Dataset:
    code: str
    table: str
    since: str  # first period requested, in Eurostat's notation
    window_start: datetime  # the same, as an instant, for raw's _window_start
    # Dimensions with a single category in every response, and that category.
    fixed: dict[str, str]


DATASETS = {
    # 2007-S1: the current consumption bands (DA to DE) start in 2007.
    "nrg_pc_204": Dataset(
        code="nrg_pc_204",
        table="eurostat_household_prices",
        since="2007-S1",
        window_start=datetime(2007, 1, 1, tzinfo=UTC),
        fixed={"freq": "S", "siec": "E7000", "unit": "KWH", "currency": "EUR"},
    ),
    # The component breakdown starts in 2017.
    "nrg_pc_204_c": Dataset(
        code="nrg_pc_204_c",
        table="eurostat_household_price_components",
        since="2017",
        window_start=datetime(2017, 1, 1, tzinfo=UTC),
        fixed={"freq": "A", "currency": "EUR"},
    ),
}

Fetch = Callable[[Dataset], dict]


def fetch_dataset(
    dataset: Dataset,
    attempts: int = 4,
    base_delay_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Download one dataset for COUNTRIES, in euros, retrying transient failures.

    A 429, a 5xx, a timeout or a dropped connection is retried with
    exponential backoff; anything else is raised at once.
    """
    params = [("format", "JSON"), ("lang", "EN"), ("currency", "EUR")]
    params += [("geo", geo) for geo in COUNTRIES]
    params.append(("sinceTimePeriod", dataset.since))
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(f"{API}/{dataset.code}", params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as error:
            status = getattr(getattr(error, "response", None), "status_code", None)
            transient = status is None or status == 429 or status >= 500
            if not transient or attempt == attempts:
                raise
            delay = base_delay_s * 2 ** (attempt - 1)
            log.warning(
                "Eurostat %s failed (%r); retry %d in %.0fs", dataset.code, error, attempt, delay
            )
            sleep(delay)
    raise AssertionError("unreachable")  # the loop either returns or raises


def observations(doc: dict) -> Iterator[tuple[dict[str, str], float, str | None]]:
    """One (categories by dimension, value, flag) per value in a JSON-stat document."""
    dims, sizes = doc["id"], doc["size"]
    codes = []
    for dim in dims:
        index = doc["dimension"][dim]["category"]["index"]
        by_position = {position: code for code, position in index.items()}
        codes.append([by_position[i] for i in range(len(by_position))])
    strides = [1] * len(dims)
    for i in range(len(dims) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    flags = doc.get("status", {})
    for flat, value in doc["value"].items():
        if value is None:
            continue
        flat_index, categories = int(flat), {}
        for dim, stride, dim_codes in zip(dims, strides, codes, strict=True):
            categories[dim] = dim_codes[(flat_index // stride) % len(dim_codes)]
        yield categories, float(value), flags.get(flat)


def updated_at(doc: dict) -> datetime:
    """When Eurostat last updated the dataset, e.g. 2026-10-19T23:00:00+0200, as UTC."""
    return datetime.fromisoformat(doc["updated"]).astimezone(UTC)


def _check(doc: dict, dataset: Dataset) -> None:
    for dim, expected in dataset.fixed.items():
        found = list(doc["dimension"][dim]["category"]["index"])
        if found != [expected]:
            raise EurostatDocumentError(
                f"{dataset.code}: expected {dim}={expected}, the response has {found}"
            )
    unknown = set(doc["dimension"]["geo"]["category"]["index"]) - set(COUNTRIES)
    if unknown:
        raise EurostatDocumentError(f"{dataset.code}: unexpected countries {sorted(unknown)}")


def parse_household_prices(doc: dict) -> list[HouseholdPriceRow]:
    _check(doc, DATASETS["nrg_pc_204"])
    source_updated_at = updated_at(doc)
    return [
        HouseholdPriceRow(
            geo=c["geo"],
            time_period=c["time"],
            nrg_cons=c["nrg_cons"],
            tax=c["tax"],
            currency=c["currency"],
            price_eur_kwh=value,
            status_flag=flag,
            source_updated_at=source_updated_at,
        )
        for c, value, flag in observations(doc)
    ]


def parse_household_price_components(doc: dict) -> list[HouseholdPriceComponentRow]:
    _check(doc, DATASETS["nrg_pc_204_c"])
    source_updated_at = updated_at(doc)
    return [
        HouseholdPriceComponentRow(
            geo=c["geo"],
            time_period=c["time"],
            nrg_cons=c["nrg_cons"],
            nrg_prc=c["nrg_prc"],
            currency=c["currency"],
            component_eur_kwh=value,
            status_flag=flag,
            source_updated_at=source_updated_at,
        )
        for c, value, flag in observations(doc)
    ]


PARSERS = {
    "nrg_pc_204": parse_household_prices,
    "nrg_pc_204_c": parse_household_price_components,
}


def newest_stored_version(con: duckdb.DuckDBPyConnection, table: str) -> datetime | None:
    row = con.execute(f"select max(source_updated_at) from {RAW_SCHEMA}.{table}").fetchone()
    return row[0].astimezone(UTC) if row and row[0] is not None else None


def load_dataset(
    con: duckdb.DuckDBPyConnection,
    code: str,
    *,
    fetch: Fetch = fetch_dataset,
    run_id: str,
    now: datetime | None = None,
) -> int:
    """Fetch one dataset and append it to raw if Eurostat has updated it since
    the last load. Returns the rows appended (0 when unchanged)."""
    dataset = DATASETS[code]
    doc = fetch(dataset)
    version = updated_at(doc)
    stored = newest_stored_version(con, dataset.table)
    if stored is not None and version <= stored:
        log.info("Eurostat %s unchanged since %s: nothing to append", code, stored)
        return 0
    rows = PARSERS[code](doc)
    return append_rows(
        con,
        dataset.table,
        rows,
        source=f"eurostat:{code}",
        window_start=dataset.window_start,
        window_end=now or datetime.now(UTC),
        run_id=run_id,
    )
