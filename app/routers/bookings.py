"""Booking creation, listing, detail and cancellation."""
import threading
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import cache
from ..auth import get_current_user
from ..database import get_db
from ..errors import AppError
from ..models import Booking, Room, User
from ..schemas import BookingCreateRequest
from ..serializers import serialize_booking
from ..services import notifications, ratelimit, reference
from ..services.refunds import log_refund
from ..timeutils import iso_utc, parse_input_datetime, utc_now

router = APIRouter(tags=["bookings"])

MIN_DURATION_HOURS = 1
MAX_DURATION_HOURS = 8
QUOTA_LIMIT = 3
QUOTA_WINDOW_HOURS = 24

# A single global lock is sufficient to serialize the read-then-write region
# for conflict and quota checks in SQLite (which only has a single-writer
# transaction model anyway). The critical section is short and the API is
# served by a single uvicorn worker in the container, so this lock keeps the
# system correct under contention without becoming a bottleneck in practice.
_BOOKING_LOCK = threading.Lock()


def _has_conflict(db: Session, room_id: int, start, end) -> bool:
    """Return True iff the room already has a confirmed booking overlapping
    ``[start, end)``.

    Overlap test follows the spec exactly:
        existing.start < new.end AND new.start < existing.end
    Back-to-back bookings (``existing.end == new.start`` or
    ``new.end == existing.start``) must NOT conflict.
    """
    overlap = (
        db.query(Booking.id)
        .filter(
            Booking.room_id == room_id,
            Booking.status == "confirmed",
            Booking.start_time < end,
            start < Booking.end_time,
        )
        .limit(1)
        .first()
    )
    return overlap is not None


def _check_quota(db: Session, user_id: int, now, start) -> None:
    window_end = now + timedelta(hours=QUOTA_WINDOW_HOURS)
    # Quota window is ``(now, now + 24h]`` per the spec; start must lie in
    # that range for the quota to apply.
    if not (now < start <= window_end):
        return
    count = (
        db.query(Booking.id)
        .filter(
            Booking.user_id == user_id,
            Booking.status == "confirmed",
            Booking.start_time > now,
            Booking.start_time <= window_end,
        )
        .count()
    )
    if count >= QUOTA_LIMIT:
        raise AppError(409, "QUOTA_EXCEEDED", "Booking quota exceeded")


def _refund_amount_cents(price_cents: int, refund_percent: int) -> int:
    """Half-up rounding on a Decimal computation so half-cents round up
    deterministically across platforms (e.g. 50% of 1001 = 501)."""
    cents = Decimal(price_cents) * Decimal(refund_percent) / Decimal(100)
    return int(cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@router.post("/bookings", status_code=201)
def create_booking(
    payload: BookingCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Rate limit first; rejected requests must still count toward the window.
    ratelimit.record_and_check(user.id)

    start = parse_input_datetime(payload.start_time)
    end = parse_input_datetime(payload.end_time)
    now = utc_now()

    # Future check: no grace window. ``start_time`` must be strictly greater
    # than the request time.
    if start <= now:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "start_time must be in the future")

    if end <= start:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "end_time must be after start_time")

    duration_hours = (end - start).total_seconds() / 3600
    if duration_hours != int(duration_hours):
        raise AppError(400, "INVALID_BOOKING_WINDOW", "duration must be a whole number of hours")
    duration_hours = int(duration_hours)
    if duration_hours < MIN_DURATION_HOURS or duration_hours > MAX_DURATION_HOURS:
        raise AppError(400, "INVALID_BOOKING_WINDOW", "duration out of range")

    # Serialize the conflict-check + insert region. The container runs a
    # single uvicorn worker so this lock is the simplest correct way to
    # prevent the classic "two transactions see no conflict, both commit"
    # race that breaks the no-double-booking and quota rules.
    booking = None
    with _BOOKING_LOCK:
        # Open an ``IMMEDIATE`` SQLite transaction so the read for the
        # conflict check holds the database write lock until commit; this
        # guarantees sequential consistency for the conflict / quota tests.
        db.execute(text("BEGIN IMMEDIATE"))

        try:
            room = db.query(Room).filter(Room.id == payload.room_id, Room.org_id == user.org_id).first()
            if room is None:
                db.rollback()
                raise AppError(404, "ROOM_NOT_FOUND", "Room not found")

            if _has_conflict(db, room.id, start, end):
                db.rollback()
                raise AppError(409, "ROOM_CONFLICT", "Room already booked for this interval")

            _check_quota(db, user.id, now, start)

            price_cents = room.hourly_rate_cents * duration_hours

            # Reference code uniqueness is enforced by the DB UNIQUE
            # constraint; on the (extremely unlikely) collision we retry
            # with a fresh code.
            for _attempt in range(5):
                try:
                    booking = Booking(
                        room_id=room.id,
                        user_id=user.id,
                        start_time=start,
                        end_time=end,
                        status="confirmed",
                        reference_code=reference.next_reference_code(),
                        price_cents=price_cents,
                        created_at=now,
                    )
                    db.add(booking)
                    db.commit()
                    db.refresh(booking)
                    break
                except IntegrityError:
                    db.rollback()
                    db.execute(text("BEGIN IMMEDIATE"))
                    continue
            else:  # pragma: no cover - extremely unlikely
                db.rollback()
                raise AppError(409, "ROOM_CONFLICT", "Could not allocate reference code")
        except Exception:
            # Roll back any open transaction on error path.
            try:
                db.rollback()
            except Exception:
                pass
            raise

    cache.invalidate_availability(room.id, start.date().isoformat())
    cache.invalidate_report(user.org_id)
    notifications.notify_created(booking)

    return serialize_booking(booking)


@router.get("/bookings")
def list_bookings(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    base = db.query(Booking).filter(Booking.user_id == user.id)
    total = base.count()
    items = (
        base.order_by(Booking.start_time.asc(), Booking.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return {
        "items": [serialize_booking(b) for b in items],
        "page": page,
        "limit": limit,
        "total": total,
    }


@router.get("/bookings/{booking_id}")
def get_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    booking = (
        db.query(Booking)
        .join(Room, Booking.room_id == Room.id)
        .filter(Booking.id == booking_id, Room.org_id == user.org_id)
        .first()
    )
    if booking is None:
        raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found")
    if user.role != "admin" and booking.user_id != user.id:
        # Another member's booking in the same org behaves as not-found.
        raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found")

    response = serialize_booking(booking)
    response["refunds"] = [
        {
            "amount_cents": r.amount_cents,
            "status": r.status,
            "processed_at": iso_utc(r.processed_at),
        }
        for r in booking.refunds
    ]
    return response


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    booking = None
    refund_percent = 0
    refund_amount_cents = 0

    with _BOOKING_LOCK:
        db.execute(text("BEGIN IMMEDIATE"))
        try:
            booking = (
                db.query(Booking)
                .join(Room, Booking.room_id == Room.id)
                .filter(Booking.id == booking_id, Room.org_id == user.org_id)
                .first()
            )
            if booking is None:
                db.rollback()
                raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found")
            if user.role != "admin" and booking.user_id != user.id:
                db.rollback()
                raise AppError(404, "BOOKING_NOT_FOUND", "Booking not found")

            if booking.status == "cancelled":
                db.rollback()
                raise AppError(409, "ALREADY_CANCELLED", "Booking already cancelled")

            now = utc_now()
            notice_seconds = (booking.start_time - now).total_seconds()
            notice_hours = notice_seconds / 3600.0

            if notice_hours >= 48:
                refund_percent = 100
            elif notice_hours >= 24:
                refund_percent = 50
            else:
                refund_percent = 0

            refund_amount_cents = _refund_amount_cents(booking.price_cents, refund_percent)

            log_refund(db, booking, refund_amount_cents)
            booking.status = "cancelled"
            db.commit()
            db.refresh(booking)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            raise

    cache.invalidate_availability(booking.room_id, booking.start_time.date().isoformat())
    cache.invalidate_report(user.org_id)
    notifications.notify_cancelled(booking)

    return {
        "id": booking.id,
        "status": "cancelled",
        "refund_percent": refund_percent,
        "refund_amount_cents": refund_amount_cents,
    }
