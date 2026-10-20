"""Which time windows a run asks ENTSO-E for. Pure functions, so they are tested
without a clock or a network."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RunWindows:
    # What is published a day ahead (prices, the load forecast) reaches into
    # tomorrow; what is measured (generation, load, flows) stops at the
    # current hour. Both start at the same instant.
    day_ahead: Window
    measured: Window


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def _floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def daily_windows(now: datetime, trailing_days: int) -> RunWindows:
    """Re-pull `trailing_days` whole UTC days to pick up revisions (ADR-003),
    up to tomorrow for day-ahead data and up to the current hour for measured data."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(UTC).date()
    start = _utc_midnight(today - timedelta(days=trailing_days))
    # Two days ahead covers tomorrow's local delivery day in every European
    # zone (it ends at 22:00 or 23:00 UTC tomorrow).
    day_ahead_end = _utc_midnight(today + timedelta(days=2))
    return RunWindows(
        day_ahead=Window(start, day_ahead_end),
        measured=Window(start, _floor_hour(now)),
    )


def backfill_windows(start_date: date, end_date: date, now: datetime) -> RunWindows:
    """Whole UTC days from start_date through end_date (inclusive). Measured
    data never asks for hours that have not happened yet."""
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")
    start = _utc_midnight(start_date)
    end = _utc_midnight(end_date + timedelta(days=1))
    measured_end = min(end, _floor_hour(now))
    if measured_end <= start:
        raise ValueError(f"The backfill starts in the future: {start_date}")
    return RunWindows(day_ahead=Window(start, end), measured=Window(start, measured_end))
