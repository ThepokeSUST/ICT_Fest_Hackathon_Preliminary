"""Helpers for parsing input datetimes and rendering UTC responses."""
from datetime import datetime, timezone


def parse_input_datetime(value: str) -> datetime:
    """Parse an ISO 8601 datetime into a naive UTC datetime for storage.

    Inputs that carry a UTC offset (including the ``Z`` designator) are
    normalized to UTC; naive inputs are treated as UTC as-is.
    """
    # ``datetime.fromisoformat`` in Python 3.11+ accepts the ``Z`` suffix
    # natively, but to stay portable and tolerate trailing ``Z`` even when
    # the parser doesn't, normalize it to ``+00:00`` first.
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is not None:
        # Convert any non-UTC offset to UTC first, then drop the tzinfo so we
        # store naive UTC datetimes consistently.
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def utc_now() -> datetime:
    """Return the current time as a naive UTC datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(dt: datetime) -> str:
    """Render a stored (naive UTC) datetime with an explicit ``Z`` designator."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # ``Z`` is the canonical UTC designator required by the API contract.
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
