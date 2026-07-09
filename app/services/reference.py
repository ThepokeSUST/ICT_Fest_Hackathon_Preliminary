"""Human-facing booking reference codes.

Reference codes must be globally unique even under concurrent creation. We
generate short, customer-friendly ``CW-`` prefixed codes from
``uuid.uuid4``; uniqueness is ultimately enforced by the database's UNIQUE
constraint on ``bookings.reference_code`` and the booking creator retries on
collision.
"""
import uuid


def next_reference_code() -> str:
    """Return a freshly-minted reference code with the ``CW-`` prefix."""
    return f"CW-{uuid.uuid4().hex[:10].upper()}"
