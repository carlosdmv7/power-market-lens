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
    # Day-ahead prices reach into tomorrow, actual generation stops at the
    # current hour, so the two entities get different end bounds.
    prices: Window
    generation: Window


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time(0), tzinfo=UTC)


def _floor_hour(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def daily_windows(now: datetime, trailing_days: int) -> RunWindows:
    """Re-pull `trailing_days` whole UTC days to pick up revisions (ADR-003),
    up to tomorrow's prices and up to the current hour for generation."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(UTC).date()
    start = _utc_midnight(today - timedelta(days=trailing_days))
    # Two days ahead covers tomorrow's local delivery day in every European
    # zone (it ends at 22:00 or 23:00 UTC tomorrow).
    prices_end = _utc_midnight(today + timedelta(days=2))
    return RunWindows(
        prices=Window(start, prices_end),
        generation=Window(start, _floor_hour(now)),
    )


def backfill_windows(start_date: date, end_date: date, now: datetime) -> RunWindows:
    """Whole UTC days from start_date through end_date (inclusive). Generation
    never asks for hours that have not happened yet."""
    if end_date < start_date:
        raise ValueError(f"end_date {end_date} is before start_date {start_date}")
    start = _utc_midnight(start_date)
    end = _utc_midnight(end_date + timedelta(days=1))
    generation_end = min(end, _floor_hour(now))
    if generation_end <= start:
        raise ValueError(f"The backfill starts in the future: {start_date}")
    return RunWindows(prices=Window(start, end), generation=Window(start, generation_end))
