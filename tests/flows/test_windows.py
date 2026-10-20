from datetime import UTC, date, datetime

import pytest

from power_market_lens.flows.windows import backfill_windows, daily_windows


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class TestDailyWindows:
    def test_trailing_days_up_to_tomorrow_for_prices_and_now_for_generation(self):
        windows = daily_windows(utc(2026, 10, 17, 14, 37), trailing_days=7)

        assert windows.day_ahead.start == utc(2026, 10, 10)
        assert windows.day_ahead.end == utc(2026, 10, 19)
        assert windows.measured.start == utc(2026, 10, 10)
        assert windows.measured.end == utc(2026, 10, 17, 14)

    def test_now_in_another_zone_is_converted_to_utc_first(self):
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        # 00:30 in Madrid on the 18th is still the 17th in UTC.
        now = datetime(2026, 10, 18, 0, 30, tzinfo=ZoneInfo("Europe/Madrid"))

        windows = daily_windows(now, trailing_days=1)

        assert windows.day_ahead.start == utc(2026, 10, 16)
        assert windows.measured.end == utc(2026, 10, 17, 22)

    def test_rejects_naive_now(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            daily_windows(datetime(2026, 10, 17), trailing_days=7)


class TestBackfillWindows:
    def test_whole_utc_days_inclusive(self):
        windows = backfill_windows(date(2026, 7, 19), date(2026, 10, 16), now=utc(2026, 10, 17, 9))

        assert windows.day_ahead.start == utc(2026, 7, 19)
        assert windows.day_ahead.end == utc(2026, 10, 17)
        assert windows.measured.end == utc(2026, 10, 17)

    def test_generation_stops_at_the_current_hour(self):
        windows = backfill_windows(
            date(2026, 10, 10), date(2026, 10, 18), now=utc(2026, 10, 17, 9, 5)
        )

        assert windows.day_ahead.end == utc(2026, 10, 19)
        assert windows.measured.end == utc(2026, 10, 17, 9)

    def test_rejects_reversed_and_future_ranges(self):
        with pytest.raises(ValueError, match="before start_date"):
            backfill_windows(date(2026, 10, 2), date(2026, 10, 1), now=utc(2026, 10, 17))
        with pytest.raises(ValueError, match="in the future"):
            backfill_windows(date(2026, 11, 1), date(2026, 11, 2), now=utc(2026, 10, 17))
