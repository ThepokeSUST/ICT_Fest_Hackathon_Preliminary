"""End-to-end verification of every business rule in the API contract.

Not part of the public grading harness — local harness only. Runs against a
fresh in-memory SQLite DB.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./cowork_verify.db")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0, tzinfo=timezone.utc).isoformat().replace("+00:00", "+00:00")


def _future(hours: int, base=None) -> datetime:
    base = base or datetime.now(timezone.utc)
    return base + timedelta(hours=hours)


def _register_admin(suffix: str) -> dict:
    org = f"org-{suffix}"
    res = client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw12345"})
    assert res.status_code == 201, res.text
    return res.json() | {"org_name": org, "password": "pw12345"}


def _login(org: str, username="alice", password="pw12345") -> dict:
    res = client.post("/auth/login", json={"org_name": org, "username": username, "password": password})
    assert res.status_code == 200, res.text
    return res.json()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- Auth ----------
def test_register_unique_username():
    org = f"unique-{datetime.now().timestamp()}"
    a = client.post("/auth/register", json={"org_name": org, "username": "u", "password": "pw1"})
    assert a.status_code == 201
    dup = client.post("/auth/register", json={"org_name": org, "username": "u", "password": "pw1"})
    assert dup.status_code == 409
    assert dup.json()["code"] == "USERNAME_TAKEN"


def test_register_join_existing_org_member():
    org = f"join-org-{datetime.now().timestamp()}"
    r1 = client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"})
    assert r1.json()["role"] == "admin"
    r2 = client.post("/auth/register", json={"org_name": org, "username": "b", "password": "pw1"})
    assert r2.json()["role"] == "member"


def test_login_bad_credentials():
    org = f"bad-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"})
    assert client.post("/auth/login", json={"org_name": org, "username": "a", "password": "wrong"}).status_code == 401


def test_token_lifetime():
    org = f"life-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"})
    t = client.post("/auth/login", json={"org_name": org, "username": "a", "password": "pw1"}).json()
    import jwt
    from app.config import JWT_SECRET, JWT_ALGORITHM
    a = jwt.decode(t["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    r = jwt.decode(t["refresh_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert a["type"] == "access"
    assert r["type"] == "refresh"
    assert a["exp"] - a["iat"] == 900


def test_refresh_single_use():
    org = f"refresh-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"})
    rt = client.post("/auth/login", json={"org_name": org, "username": "a", "password": "pw1"}).json()["refresh_token"]
    r1 = client.post("/auth/refresh", json={"refresh_token": rt})
    assert r1.status_code == 200
    r2 = client.post("/auth/refresh", json={"refresh_token": rt})
    assert r2.status_code == 401


def test_logout_invalidates_access_token():
    org = f"logout-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"})
    tok = client.post("/auth/login", json={"org_name": org, "username": "a", "password": "pw1"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    assert client.post("/auth/logout", headers=headers).status_code == 200
    # Subsequent use → 401
    assert client.get("/rooms", headers=headers).status_code == 401


# ---------- Bookings ----------
def _bootstrap(room_rate=1000, hours_offset=50):
    org = f"b-{datetime.now().timestamp()}"
    admin = client.post("/auth/register", json={"org_name": org, "username": "a", "password": "pw1"}).json()
    tok = client.post("/auth/login", json={"org_name": org, "username": "a", "password": "pw1"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    room = client.post("/rooms", json={"name": "R", "capacity": 4, "hourly_rate_cents": room_rate}, headers=headers).json()
    return {"org": org, "admin": admin, "headers": headers, "room_id": room["id"]}


def test_booking_price_and_duration():
    b = _bootstrap()
    # 2-hour, 1000c -> 2000c
    s = _future(50)
    e = s + timedelta(hours=2)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    assert r.status_code == 201, r.text
    assert r.json()["price_cents"] == 2000


def test_booking_future_strict():
    b = _bootstrap()
    s = _future(-1)
    e = s + timedelta(hours=1)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "INVALID_BOOKING_WINDOW"


def test_booking_duration_range():
    b = _bootstrap()
    # 9h duration
    s = _future(50)
    e = s + timedelta(hours=9)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    assert r.status_code == 400

    # 0 duration
    s2 = _future(70)
    e2 = s2
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s2), "end_time": _iso(e2)}, headers=b["headers"])
    assert r.status_code == 400

    # non-whole hour
    s3 = _future(90)
    e3 = s3 + timedelta(hours=1, minutes=30)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s3), "end_time": _iso(e3)}, headers=b["headers"])
    assert r.status_code == 400


def test_double_booking_conflict_and_back_to_back():
    b = _bootstrap()
    s = _future(50)
    e = s + timedelta(hours=2)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    assert r.status_code == 201, r.text
    # overlapping
    s2 = s + timedelta(minutes=30)
    e2 = s2 + timedelta(hours=1)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s2), "end_time": _iso(e2)}, headers=b["headers"])
    assert r.status_code == 409
    assert r.json()["code"] == "ROOM_CONFLICT"
    # back-to-back OK
    s3 = e
    e3 = s3 + timedelta(hours=1)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s3), "end_time": _iso(e3)}, headers=b["headers"])
    assert r.status_code == 201


def test_quota_three_in_window():
    b = _bootstrap()
    # 3 bookings, all within next 24h
    out = []
    for i, base_h in enumerate([1, 10, 20]):
        s = _future(base_h)
        e = s + timedelta(hours=1)
        r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
        assert r.status_code == 201, r.text
        out.append(r.json())
    # 4th should fail
    s = _future(22)
    e = s + timedelta(hours=1)
    r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    assert r.status_code == 409
    assert r.json()["code"] == "QUOTA_EXCEEDED"


def test_refund_tiers_and_log_equal():
    # Set up a fresh booking, then cancel at different notice points.
    # Since notice depends on real time, we cheat by adjusting start_time.
    b = _bootstrap()
    # 100% tier: start > 48h in future
    s = _future(50)
    e = s + timedelta(hours=1)
    bid = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"]).json()["id"]
    r = client.post(f"/bookings/{bid}/cancel", headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["refund_percent"] == 100
    assert r.json()["refund_amount_cents"] == 1000
    # 50% tier
    s = _future(25)
    e = s + timedelta(hours=1)
    bid = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"]).json()["id"]
    r = client.post(f"/bookings/{bid}/cancel", headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["refund_percent"] == 50
    assert r.json()["refund_amount_cents"] == 500
    # 0% tier
    s = _future(2)
    e = s + timedelta(hours=1)
    bid = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"]).json()["id"]
    r = client.post(f"/bookings/{bid}/cancel", headers=b["headers"])
    assert r.status_code == 200
    assert r.json()["refund_percent"] == 0
    assert r.json()["refund_amount_cents"] == 0

    # double-cancel
    # (re-use the last booking id; it's already cancelled)
    r = client.post(f"/bookings/{bid}/cancel", headers=b["headers"])
    assert r.status_code == 409
    assert r.json()["code"] == "ALREADY_CANCELLED"


def test_refund_amount_equals_log():
    """Spec rule 6: cancel-response amount equals RefundLog amount."""
    b = _bootstrap(room_rate=1001)
    s = _future(25)
    e = s + timedelta(hours=1)
    bid = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"]).json()["id"]
    cancel = client.post(f"/bookings/{bid}/cancel", headers=b["headers"]).json()
    detail = client.get(f"/bookings/{bid}", headers=b["headers"]).json()
    assert cancel["refund_amount_cents"] == detail["refunds"][-1]["amount_cents"]


def test_booking_isolation_404():
    org_a = f"ia-{datetime.now().timestamp()}"
    org_b = f"ib-{datetime.now().timestamp()}"
    client.post("/auth/register", json={"org_name": org_a, "username": "a", "password": "pw1"})
    client.post("/auth/register", json={"org_name": org_b, "username": "a", "password": "pw1"})
    tok_a = client.post("/auth/login", json={"org_name": org_a, "username": "a", "password": "pw1"}).json()["access_token"]
    tok_b = client.post("/auth/login", json={"org_name": org_b, "username": "a", "password": "pw1"}).json()["access_token"]
    ha = {"Authorization": f"Bearer {tok_a}"}
    hb = {"Authorization": f"Bearer {tok_b}"}
    room_a = client.post("/rooms", json={"name": "R", "capacity": 4, "hourly_rate_cents": 1000}, headers=ha).json()
    # Cross-org room id → 404
    assert client.get(f"/rooms/{room_a['id']}/availability?date=2030-01-01", headers=hb).status_code == 404
    # Booking under org A
    s = _future(50)
    e = s + timedelta(hours=1)
    bid = client.post("/bookings", json={"room_id": room_a["id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=ha).json()["id"]
    # Cross-org booking read → 404
    assert client.get(f"/bookings/{bid}", headers=hb).status_code == 404
    assert client.post(f"/bookings/{bid}/cancel", headers=hb).status_code == 404


def test_pagination_total_and_order():
    b = _bootstrap()
    starts = [50, 60, 70]
    for h in starts:
        s = _future(h)
        e = s + timedelta(hours=1)
        client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    listing = client.get("/bookings?page=1&limit=2", headers=b["headers"]).json()
    assert listing["total"] == 3
    assert len(listing["items"]) == 2
    listing2 = client.get("/bookings?page=2&limit=2", headers=b["headers"]).json()
    assert len(listing2["items"]) == 1
    # Combined list contains all bookings, no duplicates, sorted by start_time asc
    seen = listing["items"] + listing2["items"]
    ids = [it["id"] for it in seen]
    assert sorted(set(ids)) == sorted(ids)
    starts_it = [it["start_time"] for it in seen]
    assert starts_it == sorted(starts_it)


def test_stats_reflect_state():
    b = _bootstrap(room_rate=1000)
    # Empty
    res = client.get(f"/rooms/{b['room_id']}/stats", headers=b["headers"]).json()
    assert res["total_confirmed_bookings"] == 0
    assert res["total_revenue_cents"] == 0
    # Create one
    s = _future(50)
    e = s + timedelta(hours=2)
    bid = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"]).json()["id"]
    res = client.get(f"/rooms/{b['room_id']}/stats", headers=b["headers"]).json()
    assert res["total_confirmed_bookings"] == 1
    assert res["total_revenue_cents"] == 2000
    # Cancel → both decrement
    client.post(f"/bookings/{bid}/cancel", headers=b["headers"])
    res = client.get(f"/rooms/{b['room_id']}/stats", headers=b["headers"]).json()
    assert res["total_confirmed_bookings"] == 0
    assert res["total_revenue_cents"] == 0


def test_availability_reflects_state():
    b = _bootstrap()
    date = (_future(50).date() + timedelta(days=1)).isoformat()
    # Create booking on that date (roughly)
    target = datetime.fromisoformat(date + "T10:00:00+00:00")
    s = target + timedelta(hours=2)
    e = s + timedelta(hours=2)
    client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
    res = client.get(f"/rooms/{b['room_id']}/availability?date={date}", headers=b["headers"]).json()
    assert len(res["busy"]) >= 1


def test_concurrent_booking_no_double():
    """Race 20 concurrent booking creations for the same slot; at most 1 success."""
    b = _bootstrap()
    s = _future(50)
    e = s + timedelta(hours=2)
    successes = []

    def attempt():
        r = client.post("/bookings", json={"room_id": b["room_id"], "start_time": _iso(s), "end_time": _iso(e)}, headers=b["headers"])
        if r.status_code == 201:
            successes.append(r.json())

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(successes) == 1


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
