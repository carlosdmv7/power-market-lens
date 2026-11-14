from datetime import UTC, date, datetime

import duckdb
import pytest

from power_market_lens.freshness import latest_by_zone, problems, required_delivery_date


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class TestRequiredDeliveryDate:
    def test_it_is_today_in_brussels_time(self):
        assert required_delivery_date(utc(2026, 11, 14, 5, 23)) == date(2026, 11, 14)

    def test_late_in_the_utc_evening_the_brussels_day_has_already_changed(self):
        # 23:30 UTC on the 14th is 00:30 on the 15th in Brussels (winter);
        # in summer Brussels is past midnight from 22:00 UTC.
        assert required_delivery_date(utc(2026, 11, 14, 23, 30)) == date(2026, 11, 15)
        assert required_delivery_date(utc(2026, 7, 1, 22, 30)) == date(2026, 7, 2)
        assert required_delivery_date(utc(2026, 7, 1, 21, 30)) == date(2026, 7, 1)


class TestProblems:
    NOW = utc(2026, 11, 14, 5, 23)  # morning check: today's prices are due

    def test_fresh_marts_have_none(self):
        latest = {
            "ES": (date(2026, 11, 14), utc(2026, 11, 13, 13, 5)),
            "PT": (date(2026, 11, 14), utc(2026, 11, 13, 13, 5)),
        }
        assert problems(latest, ["ES", "PT"], self.NOW) == []

    def test_a_zone_behind_is_named_with_both_dates(self):
        latest = {
            "ES": (date(2026, 11, 14), utc(2026, 11, 13, 13, 5)),
            "PT": (date(2026, 11, 12), utc(2026, 11, 13, 13, 5)),
        }
        assert problems(latest, ["ES", "PT"], self.NOW) == [
            "PT: prices end on 2026-11-12, expected through 2026-11-14"
        ]

    def test_a_zone_with_no_rows_is_reported(self):
        latest = {"ES": (date(2026, 11, 14), utc(2026, 11, 13, 13, 5))}
        assert problems(latest, ["ES", "PT"], self.NOW) == ["PT: no prices in the marts at all"]

    def test_an_old_newest_load_is_reported_even_when_dates_reach(self):
        latest = {"ES": (date(2026, 11, 14), utc(2026, 11, 12, 13, 0))}
        (finding,) = problems(latest, ["ES"], self.NOW)
        assert finding.startswith("newest load is 40 hours old (2026-11-12 13:00 UTC)")

    def test_the_november_2026_outage_would_have_been_caught_on_its_first_night(self):
        # The last good load, early on 6 Nov, left prices through the 6th.
        # The first check after Brussels midnight expects the 7th.
        latest = {"ES": (date(2026, 11, 6), utc(2026, 11, 6, 1, 14))}
        assert problems(latest, ["ES"], utc(2026, 11, 6, 23, 23)) == [
            "ES: prices end on 2026-11-06, expected through 2026-11-07"
        ]

    def test_a_naive_now_is_refused(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            problems({}, ["ES"], datetime(2026, 11, 14, 5, 23))  # noqa: DTZ001


class TestLatestByZone:
    def test_reads_the_newest_day_and_load_per_zone(self):
        con = duckdb.connect()
        con.execute("set TimeZone = 'UTC'")
        con.execute("create schema marts")
        con.execute(
            """
            create table marts.fct_day_ahead_price_hourly as
            select * from (values
                ('ES', date '2026-11-13', timestamptz '2026-11-12 13:00:00+00'),
                ('ES', date '2026-11-14', timestamptz '2026-11-13 13:05:00+00'),
                ('PT', date '2026-11-14', timestamptz '2026-11-13 13:06:00+00')
            ) as t(zone_code, delivery_date_local, loaded_at)
            """
        )
        assert latest_by_zone(con) == {
            "ES": (date(2026, 11, 14), utc(2026, 11, 13, 13, 5)),
            "PT": (date(2026, 11, 14), utc(2026, 11, 13, 13, 6)),
        }

    def test_no_marts_yet_means_no_zones(self):
        assert latest_by_zone(duckdb.connect()) == {}
