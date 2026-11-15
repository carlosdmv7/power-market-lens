"""Eurostat household prices against recorded responses; no network (CLAUDE.md)."""

import copy
import csv
from datetime import UTC, datetime
import json
from pathlib import Path

import duckdb
import pytest
import requests

from power_market_lens.ingest import eurostat
from power_market_lens.ingest.warehouse import ensure_raw_tables

FIXTURES = Path(__file__).parent / "fixtures" / "eurostat"
SEED = Path(__file__).parents[2] / "transform" / "seeds" / "bidding_zones.csv"


def recorded(code: str) -> dict:
    return json.loads((FIXTURES / f"recorded_{code}.json").read_text())


@pytest.fixture
def con():
    connection = duckdb.connect()
    ensure_raw_tables(connection)
    yield connection
    connection.close()


class TestParsing:
    def test_one_row_per_published_value(self):
        for code, parse in eurostat.PARSERS.items():
            doc = recorded(code)
            assert len(parse(doc)) == len(doc["value"])

    def test_a_price_matches_a_single_cell_query(self):
        # Checked on 2026-11-15 against the API asked for this one cell
        # (geo=ES, time=2025-S1, nrg_cons=KWH2500-4999, tax=I_TAX, EUR): 0.2612.
        rows = eurostat.parse_household_prices(recorded("nrg_pc_204"))
        (row,) = [
            r
            for r in rows
            if (r.geo, r.time_period, r.nrg_cons, r.tax)
            == ("ES", "2025-S1", "KWH2500-4999", "I_TAX")
        ]
        assert row.price_eur_kwh == pytest.approx(0.2612)

    def test_a_component_matches_a_single_cell_query(self):
        # Same check for DE, 2024, band DC, network costs: 0.1147.
        rows = eurostat.parse_household_price_components(recorded("nrg_pc_204_c"))
        (row,) = [
            r
            for r in rows
            if (r.geo, r.time_period, r.nrg_cons, r.nrg_prc)
            == ("DE", "2024", "KWH2500-4999", "NETC")
        ]
        assert row.component_eur_kwh == pytest.approx(0.1147)

    def test_flags_are_kept_next_to_their_values(self):
        doc = recorded("nrg_pc_204")
        flagged_values = {k: v for k, v in doc["status"].items() if doc["value"].get(k) is not None}
        rows = eurostat.parse_household_prices(doc)
        assert sum(r.status_flag is not None for r in rows) == len(flagged_values)
        assert {r.status_flag for r in rows} - {None} <= set(flagged_values.values())

    def test_the_update_time_is_stored_in_utc(self):
        rows = eurostat.parse_household_prices(recorded("nrg_pc_204"))
        # Eurostat wrote 2026-10-19T23:00:00+0200.
        assert {r.source_updated_at for r in rows} == {datetime(2026, 10, 19, 21, tzinfo=UTC)}

    def test_an_unexpected_currency_is_refused(self):
        doc = copy.deepcopy(recorded("nrg_pc_204"))
        doc["dimension"]["currency"]["category"]["index"] = {"NAC": 0}
        with pytest.raises(eurostat.EurostatDocumentError, match="currency=EUR"):
            eurostat.parse_household_prices(doc)

    def test_the_countries_are_those_of_the_modelled_zones(self):
        with SEED.open() as seed:
            countries = {row["country_code"] for row in csv.DictReader(seed)}
        assert set(eurostat.COUNTRIES) == countries


class TestLoading:
    def test_a_new_version_is_appended_and_the_same_one_is_not(self, con):
        fetch = lambda dataset: recorded(dataset.code)  # noqa: E731

        first = eurostat.load_dataset(con, "nrg_pc_204", fetch=fetch, run_id="a")
        again = eurostat.load_dataset(con, "nrg_pc_204", fetch=fetch, run_id="b")

        assert first == len(recorded("nrg_pc_204")["value"])
        assert again == 0
        total = con.execute("select count(*) from raw.eurostat_household_prices").fetchone()[0]
        assert total == first

    def test_a_later_version_is_appended_alongside_the_first(self, con):
        old = recorded("nrg_pc_204_c")
        new = copy.deepcopy(old)
        new["updated"] = "2027-04-20T23:00:00+0200"

        eurostat.load_dataset(con, "nrg_pc_204_c", fetch=lambda _: old, run_id="a")
        appended = eurostat.load_dataset(con, "nrg_pc_204_c", fetch=lambda _: new, run_id="b")

        assert appended == len(old["value"])
        versions = con.execute(
            "select count(distinct source_updated_at) from raw.eurostat_household_price_components"
        ).fetchone()[0]
        assert versions == 2  # raw keeps both; staging will pick the latest


class TestFetching:
    @staticmethod
    def _response(status: int, body: dict | None = None) -> requests.Response:
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body or {}).encode()
        return response

    def test_transient_failures_are_retried(self, monkeypatch):
        outcomes = [self._response(503), requests.ConnectionError(), self._response(200, {"ok": 1})]
        delays = []

        def fake_get(*_, **__):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(eurostat.requests, "get", fake_get)
        doc = eurostat.fetch_dataset(eurostat.DATASETS["nrg_pc_204"], sleep=delays.append)
        assert doc == {"ok": 1}
        assert delays == [5, 10]

    def test_a_client_error_is_raised_at_once(self, monkeypatch):
        delays = []
        monkeypatch.setattr(eurostat.requests, "get", lambda *_, **__: self._response(404))
        with pytest.raises(requests.HTTPError):
            eurostat.fetch_dataset(eurostat.DATASETS["nrg_pc_204"], sleep=delays.append)
        assert delays == []

    def test_the_request_asks_for_the_modelled_countries_in_euros(self, monkeypatch):
        seen = {}

        def fake_get(url, params, timeout):
            seen.update(url=url, params=params, timeout=timeout)
            return self._response(200, {})

        monkeypatch.setattr(eurostat.requests, "get", fake_get)
        eurostat.fetch_dataset(eurostat.DATASETS["nrg_pc_204_c"])
        assert seen["url"].endswith("/nrg_pc_204_c")
        assert [v for k, v in seen["params"] if k == "geo"] == list(eurostat.COUNTRIES)
        assert ("currency", "EUR") in seen["params"]
        assert ("sinceTimePeriod", "2017") in seen["params"]
