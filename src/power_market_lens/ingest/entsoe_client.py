"""Fetch ENTSO-E documents in rate-limit-friendly chunks and parse them.

entsoe-py's `EntsoeRawClient` builds the requests and returns the raw XML; this
wrapper adds what a backfill needs on top:

- **Chunking.** A day-ahead request may span at most 100 documents (one per
  day) and any request at most a year, so long windows are split into chunks
  (30 days by default). A day-ahead request returns every delivery day it
  touches, so the day that straddles a chunk boundary arrives in both chunks
  (seen on real data: identical values). Staging keeps one copy.
- **Backoff on transient failures.** A 429 (too many requests), a 5xx, a
  timeout or a dropped connection is retried here with exponential backoff.
  entsoe-py does not retry read timeouts, and a Prefect task retry would
  re-download every chunk of the window, not just the one that failed.
- **No data is not an error.** `NoMatchingDataError` for a chunk means the
  publisher has nothing for it yet, so the chunk yields no rows.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
import logging
import time

from entsoe import EntsoeRawClient
from entsoe.exceptions import NoMatchingDataError
import pandas as pd
import requests

from power_market_lens.config import Settings
from power_market_lens.ingest.models import DayAheadPriceRow, GenerationRow
from power_market_lens.ingest.parsers import parse_day_ahead_prices, parse_generation_per_type
from power_market_lens.zones import BiddingZone

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def date_chunks(
    start: datetime, end: datetime, chunk_days: int
) -> Iterator[tuple[datetime, datetime]]:
    """Split [start, end) into consecutive windows of at most `chunk_days` days."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if start >= end:
        raise ValueError(f"Empty window: start {start} is not before end {end}")
    step = timedelta(days=chunk_days)
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + step, end)
        yield chunk_start, chunk_end
        chunk_start = chunk_end


def with_backoff[T](
    call: Callable[[], T],
    attempts: int = 4,
    base_delay_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run `call`, retrying transient failures with exponential backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except (requests.HTTPError, requests.Timeout, requests.ConnectionError) as error:
            if not _is_transient(error) or attempt == attempts:
                raise
            delay = base_delay_s * 2 ** (attempt - 1)
            log.warning("ENTSO-E request failed (%r); retry %d in %.0fs", error, attempt, delay)
            sleep(delay)
    raise AssertionError("unreachable")  # the loop either returns or raises


def _is_transient(error: requests.RequestException) -> bool:
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        return status in RETRYABLE_STATUS
    return True  # a timeout or a dropped connection


class EntsoeClient:
    def __init__(
        self,
        raw_client: EntsoeRawClient,
        chunk_days: int = 30,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._raw = raw_client
        self._chunk_days = chunk_days
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> "EntsoeClient":
        if settings.entsoe_api_key is None:
            raise RuntimeError("ENTSOE_API_KEY is not set; see .env.example")
        raw = EntsoeRawClient(api_key=settings.entsoe_api_key.get_secret_value(), timeout=60)
        return cls(raw)

    def day_ahead_prices(
        self, zone: BiddingZone, start: datetime, end: datetime
    ) -> list[DayAheadPriceRow]:
        def fetch(chunk_start: pd.Timestamp, chunk_end: pd.Timestamp) -> str:
            return self._raw.query_day_ahead_prices(
                zone.eic, start=chunk_start, end=chunk_end, sequence=zone.sdac_sequence
            )

        return self._fetch_chunked(
            fetch, parse_day_ahead_prices, start, end, document="A44", zone=zone
        )

    def generation_per_type(
        self, zone: BiddingZone, start: datetime, end: datetime
    ) -> list[GenerationRow]:
        def fetch(chunk_start: pd.Timestamp, chunk_end: pd.Timestamp) -> str:
            return self._raw.query_generation(zone.eic, start=chunk_start, end=chunk_end)

        return self._fetch_chunked(
            fetch, parse_generation_per_type, start, end, document="A75", zone=zone
        )

    def _fetch_chunked[T](
        self,
        fetch: Callable[[pd.Timestamp, pd.Timestamp], str],
        parse: Callable[[str], list[T]],
        start: datetime,
        end: datetime,
        *,
        document: str,
        zone: BiddingZone,
    ) -> list[T]:
        # entsoe-py formats request bounds as YYYYMMDDhh00, silently dropping
        # minutes: an off-the-hour window would be truncated, not rejected.
        for bound in (start, end):
            if (bound.minute, bound.second, bound.microsecond) != (0, 0, 0):
                raise ValueError(f"Window bounds must be on the hour, got {bound}")
        rows: list[T] = []
        for chunk_start, chunk_end in date_chunks(start, end, self._chunk_days):
            try:
                xml_text = with_backoff(
                    lambda s=chunk_start, e=chunk_end: fetch(pd.Timestamp(s), pd.Timestamp(e)),
                    sleep=self._sleep,
                )
            except NoMatchingDataError:
                log.info(
                    "%s %s %s–%s: no data published", document, zone.code, chunk_start, chunk_end
                )
                continue
            chunk_rows = parse(xml_text)
            log.info(
                "%s %s %s–%s: %d points",
                document,
                zone.code,
                chunk_start,
                chunk_end,
                len(chunk_rows),
            )
            rows.extend(chunk_rows)
        return rows
