from datetime import UTC, datetime
from pathlib import Path

from entsoe.exceptions import NoMatchingDataError
import pytest
import requests

from power_market_lens.config import Settings
from power_market_lens.ingest.entsoe_client import (
    EntsoeClient,
    date_chunks,
    redact,
    with_backoff,
)
from power_market_lens.zones import get_zone

FIXTURES = Path(__file__).parent / "fixtures" / "entsoe"


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _status(code: int) -> requests.Response:
    response = requests.Response()
    response.status_code = code
    return response


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


class FakeRawClient:
    """Stands in for EntsoeRawClient: records calls, replays canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, **call):
        self.calls.append(call)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def query_day_ahead_prices(self, country_code, start, end, sequence=None):
        return self._next(zone=country_code, start=start, end=end, sequence=sequence)

    def query_generation(self, country_code, start, end):
        return self._next(zone=country_code, start=start, end=end)


class TestDateChunks:
    def test_splits_into_bounded_contiguous_windows(self):
        chunks = list(date_chunks(utc(2026, 1, 1), utc(2026, 3, 15), chunk_days=30))

        assert chunks == [
            (utc(2026, 1, 1), utc(2026, 1, 31)),
            (utc(2026, 1, 31), utc(2026, 3, 2)),
            (utc(2026, 3, 2), utc(2026, 3, 15)),
        ]

    def test_rejects_naive_and_empty_windows(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            list(date_chunks(datetime(2026, 1, 1), utc(2026, 1, 2), 30))
        with pytest.raises(ValueError, match="Empty window"):
            list(date_chunks(utc(2026, 1, 2), utc(2026, 1, 2), 30))


class TestBackoff:
    def test_retries_throttling_with_exponential_delays(self):
        delays = []
        outcomes = [http_error(429), http_error(503), "ok"]

        def call():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert with_backoff(call, base_delay_s=5, sleep=delays.append) == "ok"
        assert delays == [5, 10]

    def test_any_server_error_is_retried(self):
        # ENTSO-E's gateway answers 599 when the platform is overloaded.
        delays = []
        outcomes = [http_error(599), "ok"]

        def call():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert with_backoff(call, base_delay_s=5, sleep=delays.append) == "ok"
        assert delays == [5]

    def test_client_errors_are_not_retried(self):
        delays = []

        def call():
            raise http_error(401)

        with pytest.raises(requests.HTTPError):
            with_backoff(call, sleep=delays.append)
        assert delays == []

    def test_timeouts_and_dropped_connections_are_retried(self):
        # entsoe-py does not retry a read timeout; without this, a Prefect task
        # retry would re-download every chunk of a multi-year window.
        delays = []
        outcomes = [requests.ReadTimeout(), requests.ConnectionError(), "ok"]

        def call():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        assert with_backoff(call, base_delay_s=5, sleep=delays.append) == "ok"
        assert delays == [5, 10]

    def test_gives_up_after_the_last_attempt(self):
        delays = []

        def call():
            raise http_error(503)

        with pytest.raises(requests.HTTPError):
            with_backoff(call, attempts=3, sleep=delays.append)
        assert delays == [5, 10]


class TestTheTokenNeverReachesALog:
    URL = "https://web-api.tp.entsoe.eu/api?documentType=A65&securityToken=s3cr3t-t0ken&x=1"

    def test_redact_hides_the_token_only(self):
        assert redact(self.URL) == self.URL.replace("s3cr3t-t0ken", "***")

    def test_a_raised_error_does_not_carry_it(self):
        def call():
            raise requests.HTTPError(f"401 Client Error for url: {self.URL}", response=_status(401))

        with pytest.raises(requests.HTTPError) as raised:
            with_backoff(call, sleep=lambda _: None)
        assert "s3cr3t-t0ken" not in str(raised.value)
        assert raised.value.response.status_code == 401
        assert raised.value.__suppress_context__  # the original is not printed either

    def test_retry_warnings_do_not_carry_it(self, caplog):
        outcomes = [requests.ConnectionError(f"Max retries exceeded with url: {self.URL}"), "ok"]

        def call():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        with caplog.at_level("WARNING"):
            assert with_backoff(call, sleep=lambda _: None) == "ok"
        assert "retry 1" in caplog.text
        assert "s3cr3t-t0ken" not in caplog.text


class TestEntsoeClient:
    def test_fetches_each_chunk_and_skips_ones_without_data(self):
        xml = (FIXTURES / "synthetic_prices_a03_15min_dst_autumn.xml").read_text()
        raw = FakeRawClient([xml, NoMatchingDataError()])
        client = EntsoeClient(raw, chunk_days=1, sleep=lambda _: None)

        rows = client.day_ahead_prices(get_zone("ES"), utc(2025, 10, 25, 22), utc(2025, 10, 27, 22))

        assert len(rows) == 100
        assert len(raw.calls) == 2
        assert raw.calls[0]["zone"] == "10YES-REE------0"
        assert raw.calls[0]["sequence"] is None

    def test_generation_goes_through_the_generation_parser(self):
        xml = (FIXTURES / "synthetic_generation_per_type.xml").read_text()
        client = EntsoeClient(FakeRawClient([xml]), sleep=lambda _: None)

        rows = client.generation_per_type(get_zone("ES"), utc(2026, 7, 1), utc(2026, 7, 2))

        assert {r.psr_type for r in rows} == {"B10", "B16"}

    def test_rejects_windows_off_the_hour(self):
        client = EntsoeClient(FakeRawClient([]), sleep=lambda _: None)

        with pytest.raises(ValueError, match="on the hour"):
            client.day_ahead_prices(get_zone("ES"), utc(2026, 7, 1, 10, 30), utc(2026, 7, 2))

    def test_requires_an_api_key(self):
        with pytest.raises(RuntimeError, match="ENTSOE_API_KEY"):
            EntsoeClient.from_settings(Settings(_env_file=None, entsoe_api_key=None))
