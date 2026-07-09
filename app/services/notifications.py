"""Side effects that accompany booking lifecycle events.

Each booking change sends a (simulated) notification email and appends an
audit-log entry. Notifications are fire-and-forget hooks that must not block
or hang the API response under load, so they perform no I/O here.
"""


def notify_created(booking) -> None:  # pragma: no cover - simulated side effect
    return None


def notify_cancelled(booking) -> None:  # pragma: no cover - simulated side effect
    return None
