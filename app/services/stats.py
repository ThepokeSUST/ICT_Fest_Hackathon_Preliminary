"""Live per-room booking statistics.

Per rule 14, ``/rooms/{id}/stats`` must always equal the values derivable
from the current confirmed bookings in the database. We therefore compute the
aggregate directly on each call to avoid any drift between an in-memory
counter and the committed state.
"""
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Booking


def get_for_room(db: Session, room_id: int) -> dict:
    """Return ``{"count", "revenue"}`` for the room's confirmed bookings."""
    count, revenue = (
        db.query(
            func.count(Booking.id),
            func.coalesce(func.sum(Booking.price_cents), 0),
        )
        .filter(Booking.room_id == room_id, Booking.status == "confirmed")
        .one()
    )
    return {"count": int(count or 0), "revenue": int(revenue or 0)}


# Backwards-compatible helpers retained for any callers that still touch the
# old in-memory store; they are no-ops now that the source of truth is the DB.
def record_create(room_id: int, price_cents: int) -> None:
    return None


def record_cancel(room_id: int, price_cents: int) -> None:
    return None


def get(room_id: int) -> dict:  # pragma: no cover - superseded by get_for_room
    return {"count": 0, "revenue": 0}
