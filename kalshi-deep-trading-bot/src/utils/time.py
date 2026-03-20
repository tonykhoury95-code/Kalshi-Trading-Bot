"""
Time utilities for the trading bot.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def hours_until(dt_str: str) -> float:
    """Calculate hours from now until the given ISO 8601 datetime string.

    Args:
        dt_str: ISO 8601 datetime string (e.g. ``2025-06-01T12:00:00Z``).

    Returns:
        Hours until the specified time.  Negative if the time has passed.
    """
    if dt_str.endswith("Z"):
        dt_str = dt_str[:-1] + "+00:00"
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    delta = dt - utc_now()
    return delta.total_seconds() / 3600.0


def format_duration(hours: float) -> str:
    """Format a duration in hours to a human-readable string.

    Examples:
        - 0.5  -> "30m"
        - 2.5  -> "2h 30m"
        - 50.0 -> "2d 2h"
    """
    if hours < 0:
        return "expired"

    total_minutes = int(hours * 60)

    if total_minutes < 60:
        return f"{total_minutes}m"

    total_hours = int(hours)
    minutes = total_minutes % 60

    if total_hours < 24:
        if minutes > 0:
            return f"{total_hours}h {minutes}m"
        return f"{total_hours}h"

    days = total_hours // 24
    remaining_hours = total_hours % 24

    if remaining_hours > 0:
        return f"{days}d {remaining_hours}h"
    return f"{days}d"


def is_in_time_window(start_hour: int, end_hour: int) -> bool:
    """Check if the current UTC hour falls within a time window.

    Handles windows that wrap around midnight (e.g. start=22, end=6).

    Args:
        start_hour: Window start (0-23 UTC).
        end_hour: Window end (0-23 UTC).

    Returns:
        True if the current UTC hour is within the window.
    """
    current_hour = utc_now().hour

    if start_hour <= end_hour:
        # Normal window (e.g. 9 to 17)
        return start_hour <= current_hour < end_hour
    else:
        # Wraps around midnight (e.g. 22 to 6)
        return current_hour >= start_hour or current_hour < end_hour
