"""Refund bookkeeping.

When a booking is cancelled a refund is calculated from its price and the
applicable notice tier, then written to the refund ledger with a processed
status. Amounts are stored in whole cents; the caller is responsible for
rounding (so the row and the API response agree exactly).
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Booking, RefundLog


def log_refund(db: Session, booking: Booking, amount_cents: int) -> RefundLog:
    """Append a ``RefundLog`` row for ``booking`` with the given amount.

    The transaction is left to the caller so the log insert and the booking
    status flip can be committed atomically.
    """
    entry = RefundLog(
        booking_id=booking.id,
        amount_cents=amount_cents,
        status="processed",
        processed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(entry)
    db.flush()
    db.refresh(entry)
    return entry
