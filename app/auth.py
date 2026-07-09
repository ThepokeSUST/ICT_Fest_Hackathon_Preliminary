"""Authentication: password hashing, JWT issue/verify, request dependencies."""
import hashlib
import hmac
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .config import (
    JWT_ALGORITHM,
    JWT_SECRET,
    REFRESH_TOKEN_EXPIRE_DAYS,
)
from .database import get_db
from .errors import AppError
from .models import User

# Access token ``jti``s that have been explicitly revoked via /auth/logout, and
# refresh-token ``jti``s that have been consumed by /auth/refresh. Both sets
# are guarded by a lock so concurrent requests see consistent state.
_token_lock = threading.Lock()
_revoked_access_jtis: set[str] = set()
_used_refresh_jtis: set[str] = set()

# Required lifetimes per the API contract.
ACCESS_TOKEN_LIFETIME_SECONDS = 900  # 15 minutes
REFRESH_TOKEN_LIFETIME_SECONDS = REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600  # 7 days

_PBKDF2_ROUNDS = 100_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}:{dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split(":")
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), dk_hex)


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _encode(payload: dict) -> str:
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    iat = _now_ts()
    payload = {
        "sub": str(user.id),
        "org": user.org_id,
        "role": user.role,
        "jti": uuid.uuid4().hex,
        "iat": iat,
        "exp": iat + ACCESS_TOKEN_LIFETIME_SECONDS,
        "type": "access",
    }
    return _encode(payload)


def create_refresh_token(user: User) -> str:
    iat = _now_ts()
    payload = {
        "sub": str(user.id),
        "org": user.org_id,
        "role": user.role,
        "jti": uuid.uuid4().hex,
        "iat": iat,
        "exp": iat + REFRESH_TOKEN_LIFETIME_SECONDS,
        "type": "refresh",
    }
    return _encode(payload)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid or expired token")


def revoke_access_token(payload: dict) -> None:
    with _token_lock:
        _revoked_access_jtis.add(payload["jti"])


def consume_refresh_token(payload: dict) -> bool:
    """Mark a refresh token ``jti`` as used.

    Returns ``True`` if this was the first time the jti was seen (rotation
    succeeds), or ``False`` if the jti was already consumed (reuse -> 401).
    """
    with _token_lock:
        if payload["jti"] in _used_refresh_jtis:
            return False
        _used_refresh_jtis.add(payload["jti"])
        return True


def get_token_payload(request: Request) -> dict:
    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        raise AppError(401, "INVALID_CREDENTIALS", "Missing bearer token")
    token = header[len("Bearer "):].strip()
    payload = decode_token(token)
    if payload.get("type") != "access":
        raise AppError(401, "INVALID_CREDENTIALS", "Wrong token type")
    with _token_lock:
        revoked = payload.get("jti") in _revoked_access_jtis
    if revoked:
        raise AppError(401, "INVALID_CREDENTIALS", "Token has been revoked")
    return payload


def get_current_user(
    payload: dict = Depends(get_token_payload),
    db: Session = Depends(get_db),
) -> User:
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if user is None:
        raise AppError(401, "INVALID_CREDENTIALS", "Unknown user")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Admin privileges required")
    return user
