"""The app's warehouse access: one cached connection, one cursor per query."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import duckdb
import pytest
import streamlit as st

sys.path.insert(0, str(Path(__file__).parents[2] / "app"))  # the app imports its helpers flat

import data


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create table numbers as select range as n from range(100000)")
    con.close()
    monkeypatch.setenv("WAREHOUSE_PATH", str(path))
    # A session zone that is not UTC, as on a laptop in Madrid.
    monkeypatch.setenv("TZ", "Europe/Madrid")
    st.cache_resource.clear()
    yield path
    st.cache_resource.clear()


def test_every_query_runs_in_utc_whatever_the_machine_zone(warehouse):
    zone = data._query("select current_setting('TimeZone') as zone")["zone"].iloc[0]

    assert zone == "UTC"


def test_queries_from_many_threads_share_the_connection_safely(warehouse):
    # Streamlit serves each visitor from its own thread. Sixteen threads each
    # run the same aggregate several times through the one cached connection.
    def total(_):
        return int(data._query("select sum(n) as total from numbers")["total"].iloc[0])

    with ThreadPoolExecutor(max_workers=16) as pool:
        totals = list(pool.map(total, range(64)))

    assert totals == [sum(range(100000))] * 64
