"""Local-time display for stored UTC timestamps (v3.8.10).

Timestamps are stored as naive UTC (datetime.utcnow). They are shown in the
container's time zone, the TZ environment variable (e.g. America/New_York),
falling back to UTC when TZ is unset or unknown. Items added after 8 PM
Eastern used to show the next day's date.
"""
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def local_zone():
    """ZoneInfo for $TZ, or UTC."""
    name = (os.environ.get('TZ') or '').strip()
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def to_local(value):
    """A stored (naive UTC) datetime as an aware datetime in the local zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(local_zone())


def local_today() -> date:
    """Today's date where the user is, not in UTC."""
    return datetime.now(local_zone()).date()


def localtime_filter(value, fmt='%Y-%m-%d %H:%M', default='-'):
    """Jinja filter: {{ item.created_at|localtime('%Y-%m-%d') }}."""
    local = to_local(value)
    return local.strftime(fmt) if local else default
