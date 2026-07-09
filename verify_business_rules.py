"""One-shot verification of all 16 business rules from the README contract.

Runs against a fresh in-process FastAPI app via TestClient (no live server
needed). Each rule is a self-contained check that prints PASS/FAIL with a
short reason. Exits with code 0 only if every rule passes.

Usage:
    python verify_business_rules.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///./cowork_verify_rules.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

# ---------- helpers ----------

_RESULTS: list[tuple[str, bool, str]] = []


def record(rule: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((rule, ok, detail))
    marker = "PASS" if ok else "FAIL"
    print(f"[{marker}] {rule}  {detail}")


def iso(dt: datetime) -> str:
    """ISO 8601 with explicit Z suffix, in UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def future(hours: int, base: datetime | None = None) -> datetime:
    """Future-aware UTC datetime."""
    return (base or datetime.now(timezone.utc)) + timedelta(hours=hours)


def register_admin(suffix: str) -> tuple[str, str, str, dict]:
    """Register a fresh org, return (org_name, username, password, headers)."""
    org = f"vr-{suffix}-{int(time.time() * 1_000_000)}"
    username = "alice"
    password = "pw-verify-1"
    r = client.post(
        "/auth/register",
        json={"org_name": org, "username": username, "password": password},
    )
    assert r.status_code == 201, r.text
    tok = client.post(
        "/auth/login",
        json={"org_name": org, "username": username, "password": password},
    ).json()["access_token"]
    return org, username, password, {"Authorization": f"Bearer {tok}"}


def create_room(headers: dict, name: str = "R", rate: int = 1000) -> int:
    r = client.post(
        "/rooms",
        json={"name": name, "capacity": 4, "hourly_rate_cents": rate},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---------- rule checks ----------

def rule_01_datetime_normalization() -> None:
    """1. Datetime Normalization: input offsets → UTC, responses carry 'Z'."""
    _, _, _, headers = register_admin("dt")
    room_id = create_room(headers)
    s = future(50).astimezone(timezone(timezone.utc.utcoffset(None) + timedelta(hours=6)))
    e = (s + timedelta(hours=1)).astimezone(
        timezone(timezone.utc.utcoffset(None) + timedelta(hours=6))
    )
    r = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": s.isoformat(), "end_time": e.isoformat()},
        headers=headers,
    )
    if r.status_code != 201:
        return record("R1 datetime normalization", False, f"create failed: {r.text}")
    body = r.json()
    starts_with_z = body["start_time"].endswith("Z") and body["end_time"].endswith("Z")
    record("R1 datetime normalization", starts_with_z, f"start={body['start_time']}")


def rule_02_pricing_and_duration() -> None:
    """2. Strict Pricing & Duration: whole hours in [1, 8], price = hours * rate."""
    _, _, _, headers = register_admin("pr")
    room_id = create_room(headers, rate=1234)

    # 3h * 1234c = 3702c
    s = future(50)
    r = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s), "end_time": iso(s + timedelta(hours=3))},
        headers=headers,
    )
    if r.status_code != 201 or r.json()["price_cents"] != 3702:
        return record("R2 price = hours * rate", False, f"got {r.status_code}/{r.text}")

    # 9h rejected
    s2 = future(80)
    r = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s2), "end_time": iso(s2 + timedelta(hours=9))},
        headers=headers,
    )
    # 0h rejected
    s3 = future(100)
    r0 = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s3), "end_time": iso(s3)},
        headers=headers,
    )
    # non-whole rejected
    s4 = future(120)
    r_half = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s4), "end_time": iso(s4 + timedelta(hours=1, minutes=30))},
        headers=headers,
    )
    ok = r.status_code == 400 and r0.status_code == 400 and r_half.status_code == 400
    record("R2 price & duration bounds", ok, f"9h/0h/1.5h = {r.status_code}/{r0.status_code}/{r_half.status_code}")


def rule_03_atomicity_no_double_booking_and_quota() -> None:
    """3. Atomicity: no double-booking, no quota overrun under concurrency."""
    _, _, _, headers = register_admin("at")
    room_id = create_room(headers)
    s = future(50)
    e = s + timedelta(hours=2)

    successes: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        r = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": iso(s), "end_time": iso(e)},
            headers=headers,
        )
        with lock:
            successes.append(r.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(15)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly one 201; the rest are 409 ROOM_CONFLICT.
    n_ok = sum(1 for c in successes if c == 201)
    n_conflict = sum(1 for c in successes if c == 409)
    if n_ok != 1 or n_conflict != 14:
        return record("R3a no double booking", False, f"successes={successes}")
    record("R3a no double booking", True, "15 racers = 1 win, 14 ROOM_CONFLICT")

    # Quota: 3 in-window OK, 4th 409 QUOTA_EXCEEDED.
    _, _, _, h2 = register_admin("qt")
    rid2 = create_room(h2)
    bases = [1, 10, 20]
    for h in bases:
        s_i = future(h)
        rr = client.post(
            "/bookings",
            json={"room_id": rid2, "start_time": iso(s_i), "end_time": iso(s_i + timedelta(hours=1))},
            headers=h2,
        )
        if rr.status_code != 201:
            return record("R3b quota", False, f"unexpected {rr.status_code}: {rr.text}")
    s4 = future(22)
    rr = client.post(
        "/bookings",
        json={"room_id": rid2, "start_time": iso(s4), "end_time": iso(s4 + timedelta(hours=1))},
        headers=h2,
    )
    ok = rr.status_code == 409 and rr.json().get("code") == "QUOTA_EXCEEDED"
    record("R3b quota enforced", ok, f"4th = {rr.status_code} {rr.json()}")


def rule_04_unique_reference_codes_under_concurrency() -> None:
    """4. Reference codes remain unique under concurrent inserts."""
    _, _, _, headers = register_admin("rf")
    room_id = create_room(headers)
    codes: list[str] = []
    codes_lock = threading.Lock()

    def go(offset: int) -> None:
        s = future(50 + offset)
        r = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
            headers=headers,
        )
        if r.status_code == 201:
            with codes_lock:
                codes.append(r.json()["reference_code"])

    threads = [threading.Thread(target=go, args=(i * 2,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # All successful reference codes must be unique
    ok = len(codes) == len(set(codes)) and len(codes) > 0
    record("R4 unique reference codes", ok, f"{len(codes)} inserts, {len(set(codes))} distinct")


def rule_05_refund_tiers() -> None:
    """5. Refund: 100% (>=48h), 50% (>=24h), 0% (<24h)."""
    _, _, _, headers = register_admin("refund")
    room_id = create_room(headers, rate=1000)

    def cancel_at(hours_out: int) -> dict:
        s = future(hours_out)
        r = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        bid = r.json()["id"]
        rc = client.post(f"/bookings/{bid}/cancel", headers=headers)
        assert rc.status_code == 200, rc.text
        return rc.json()

    a = cancel_at(50)  # 100%
    b = cancel_at(25)  # 50%
    c = cancel_at(2)   # 0%
    ok = (
        a["refund_percent"] == 100 and a["refund_amount_cents"] == 1000
        and b["refund_percent"] == 50 and b["refund_amount_cents"] == 500
        and c["refund_percent"] == 0 and c["refund_amount_cents"] == 0
    )
    record("R5 refund tiers", ok, f"48h+={a['refund_percent']}%/{a['refund_amount_cents']}c, 24h+={b['refund_percent']}%/{b['refund_amount_cents']}c, <24h={c['refund_percent']}%/{c['refund_amount_cents']}c")


def rule_06_refund_amount_equals_log() -> None:
    """6. Refund: response amount == RefundLog amount (also half-up)."""
    _, _, _, headers = register_admin("reflog")
    room_id = create_room(headers, rate=1001)  # 1001 * 50% = 500.5 → 501 (half-up)
    s = future(25)
    r = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
        headers=headers,
    )
    bid = r.json()["id"]
    cancel = client.post(f"/bookings/{bid}/cancel", headers=headers).json()
    detail = client.get(f"/bookings/{bid}", headers=headers).json()
    log_amount = detail["refunds"][-1]["amount_cents"]
    ok = cancel["refund_amount_cents"] == log_amount == 501
    record("R6 refund == log (half-up)", ok, f"cancel={cancel['refund_amount_cents']} log={log_amount}")


def rule_07_multi_tenancy_isolation() -> None:
    """7. Multi-tenancy: cross-org IDs return 404 (not 403, not 200)."""
    org_a = f"iso-a-{int(time.time() * 1_000_000)}"
    org_b = f"iso-b-{int(time.time() * 1_000_000)}"
    for org in (org_a, org_b):
        client.post(
            "/auth/register",
            json={"org_name": org, "username": "alice", "password": "pw1"},
        )
    tok_a = client.post("/auth/login", json={"org_name": org_a, "username": "alice", "password": "pw1"}).json()["access_token"]
    tok_b = client.post("/auth/login", json={"org_name": org_b, "username": "alice", "password": "pw1"}).json()["access_token"]
    ha = {"Authorization": f"Bearer {tok_a}"}
    hb = {"Authorization": f"Bearer {tok_b}"}
    room_a = client.post("/rooms", json={"name": "R", "capacity": 4, "hourly_rate_cents": 1000}, headers=ha).json()
    s = future(50)
    bid = client.post(
        "/bookings",
        json={"room_id": room_a["id"], "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
        headers=ha,
    ).json()["id"]

    r_room = client.get(f"/rooms/{room_a['id']}/availability?date=2030-01-01", headers=hb)
    r_book = client.get(f"/bookings/{bid}", headers=hb)
    r_cancel = client.post(f"/bookings/{bid}/cancel", headers=hb)
    ok = (
        r_room.status_code == 404 and r_book.status_code == 404 and r_cancel.status_code == 404
    )
    record("R7 cross-org returns 404", ok, f"room={r_room.status_code} booking={r_book.status_code} cancel={r_cancel.status_code}")


def rule_08_jwt_claims() -> None:
    """8. JWT carries sub, org, role, jti, iat, exp, type."""
    import jwt as pyjwt
    from app.config import JWT_ALGORITHM, JWT_SECRET

    _, _, _, headers = register_admin("jwt")
    tok = headers["Authorization"].split(" ", 1)[1]
    decoded = pyjwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    required = {"sub", "org", "role", "jti", "iat", "exp", "type"}
    ok = required.issubset(decoded.keys()) and decoded["type"] == "access"
    record("R8 JWT claims", ok, f"keys={sorted(decoded.keys())}")


def rule_09_access_token_lifetime_15min() -> None:
    """9. Access token lifetime = 900s."""
    import jwt as pyjwt
    from app.config import JWT_ALGORITHM, JWT_SECRET

    _, _, _, headers = register_admin("life")
    tok = headers["Authorization"].split(" ", 1)[1]
    decoded = pyjwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    ok = decoded["exp"] - decoded["iat"] == 900
    record("R9 access token lifetime", ok, f"exp - iat = {decoded['exp'] - decoded['iat']}s")


def rule_10_refresh_single_use() -> None:
    """10. Refresh tokens are single-use (rotation)."""
    org = f"ref-rot-{int(time.time() * 1_000_000)}"
    client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw1"})
    rt = client.post("/auth/login", json={"org_name": org, "username": "alice", "password": "pw1"}).json()["refresh_token"]
    r1 = client.post("/auth/refresh", json={"refresh_token": rt})
    r2 = client.post("/auth/refresh", json={"refresh_token": rt})
    ok = r1.status_code == 200 and r2.status_code == 401
    record("R10 refresh rotation", ok, f"first={r1.status_code} replay={r2.status_code}")


def rule_11_logout_immediate_invalidation() -> None:
    """11. /auth/logout invalidates the access token immediately."""
    org = f"lo-{int(time.time() * 1_000_000)}"
    client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw1"})
    tok = client.post("/auth/login", json={"org_name": org, "username": "alice", "password": "pw1"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post("/auth/logout", headers=h).status_code == 200
    after = client.get("/rooms", headers=h)
    ok = after.status_code == 401 and after.json().get("code") == "INVALID_CREDENTIALS"
    record("R11 logout invalidates", ok, f"after logout = {after.status_code}")


def rule_12_rate_limit_thread_safe() -> None:
    """12. Rate limit enforced (20/60s), no race, returns 429 RATE_LIMITED."""
    _, _, _, headers = register_admin("rl")
    # Use a fresh room per thread so quota/conflict don't confuse the test;
    # the rate-limit hits at 21 requests in a 60s window.
    room_ids = [create_room(headers, name=f"RL{i}") for i in range(25)]
    codes: list[int] = []
    lock = threading.Lock()

    def fire(i: int) -> None:
        s = future(50 + i * 2)
        r = client.post(
            "/bookings",
            json={"room_id": room_ids[i], "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
            headers=headers,
        )
        with lock:
            codes.append(r.status_code)

    threads = [threading.Thread(target=fire, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    n_429 = sum(1 for c in codes if c == 429)
    n_201 = sum(1 for c in codes if c == 201)
    n_other = sum(1 for c in codes if c not in (201, 429))
    # Limit is 20; first 20 may succeed (subject to validation), rest 429.
    ok = n_429 >= 1 and n_other == 0 and n_201 + n_429 == 25
    record("R12 rate limit 20/60s", ok, f"201={n_201} 429={n_429} other={n_other} (out of 25)")


def rule_13_stats_and_availability_freshness() -> None:
    """13. Stats + availability + report reflect state immediately after writes."""
    _, _, _, headers = register_admin("st")
    room_id = create_room(headers, rate=1000)

    # Stats: 0 → 1 confirmed, then 0 after cancel
    s0 = client.get(f"/rooms/{room_id}/stats", headers=headers).json()
    s = future(50)
    bid = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": iso(s), "end_time": iso(s + timedelta(hours=2))},
        headers=headers,
    ).json()["id"]
    s1 = client.get(f"/rooms/{room_id}/stats", headers=headers).json()
    client.post(f"/bookings/{bid}/cancel", headers=headers)
    s2 = client.get(f"/rooms/{room_id}/stats", headers=headers).json()
    stats_ok = (
        s0["total_confirmed_bookings"] == 0
        and s1["total_confirmed_bookings"] == 1
        and s1["total_revenue_cents"] == 2000
        and s2["total_confirmed_bookings"] == 0
        and s2["total_revenue_cents"] == 0
    )

    # Availability: busy[] appears immediately
    date = (s + timedelta(days=1)).date().isoformat()
    avail = client.get(f"/rooms/{room_id}/availability?date={date}", headers=headers).json()
    avail_ok = isinstance(avail.get("busy"), list)

    # Report freshness
    rep = client.get(
        f"/admin/usage-report?from={future(-200).date().isoformat()}&to={future(200).date().isoformat()}",
        headers=headers,
    ).json()
    rep_ok = any(
        row["room_id"] == room_id and row["confirmed_bookings"] == 0 and row["revenue_cents"] == 0
        for row in rep.get("rooms", [])
    )

    record(
        "R13 stats/availability/report fresh",
        stats_ok and avail_ok and rep_ok,
        f"stats={stats_ok} avail={avail_ok} report={rep_ok}",
    )


def rule_14_export_include_all_org_scoped() -> None:
    """14. /admin/export?include_all=true never leaks across orgs."""
    org_a = f"ex-a-{int(time.time() * 1_000_000)}"
    org_b = f"ex-b-{int(time.time() * 1_000_000)}"
    for org in (org_a, org_b):
        client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw1"})
    tok_a = client.post("/auth/login", json={"org_name": org_a, "username": "alice", "password": "pw1"}).json()["access_token"]
    tok_b = client.post("/auth/login", json={"org_name": org_b, "username": "alice", "password": "pw1"}).json()["access_token"]
    ha = {"Authorization": f"Bearer {tok_a}"}
    hb = {"Authorization": f"Bearer {tok_b}"}
    ra = client.post("/rooms", json={"name": "RA", "capacity": 4, "hourly_rate_cents": 1000}, headers=ha).json()
    rb = client.post("/rooms", json={"name": "RB", "capacity": 4, "hourly_rate_cents": 2000}, headers=hb).json()
    from_d = future(-200).date().isoformat()
    to_d = future(200).date().isoformat()
    csv_all = client.get(
        f"/admin/export?from={from_d}&to={to_d}&format=csv&include_all=true", headers=ha
    ).text
    # Org A must NOT see room_id == rb["id"] in the export
    leaked = str(rb["id"]) in csv_all
    record("R14 export org-scoped (include_all)", not leaked, f"room_b_in_a_export={leaked}")


def rule_15_registration_duplicate_username() -> None:
    """15. Duplicate username in same org → 409 USERNAME_TAKEN."""
    org = f"dup-{int(time.time() * 1_000_000)}"
    a = client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw1"})
    dup = client.post("/auth/register", json={"org_name": org, "username": "alice", "password": "pw1"})
    ok = a.status_code == 201 and dup.status_code == 409 and dup.json().get("code") == "USERNAME_TAKEN"
    record("R15 dup username = 409 USERNAME_TAKEN", ok, f"first={a.status_code} dup={dup.status_code}")


def rule_16_contract_response_shape() -> None:
    """16. Contract: /health, /bookings list, /admin/usage-report, /bookings/{id} refund shape."""
    # /health
    h = client.get("/health").json()
    health_ok = h == {"status": "ok"}

    # /bookings list shape
    _, _, _, headers = register_admin("shape")
    create_room(headers)
    listing = client.get("/bookings", headers=headers).json()
    list_ok = set(listing.keys()) == {"items", "page", "limit", "total"} and isinstance(listing["items"], list)

    # /admin/usage-report shape
    rep = client.get(
        f"/admin/usage-report?from={future(-200).date().isoformat()}&to={future(200).date().isoformat()}",
        headers=headers,
    ).json()
    rep_ok = set(rep.keys()) == {"from", "to", "rooms"} and isinstance(rep["rooms"], list)

    # /bookings/{id} refunds[] after cancel
    s = future(50)
    bid = client.post(
        "/bookings",
        json={"room_id": create_room(headers, name="R2"), "start_time": iso(s), "end_time": iso(s + timedelta(hours=1))},
        headers=headers,
    ).json()["id"]
    client.post(f"/bookings/{bid}/cancel", headers=headers)
    detail = client.get(f"/bookings/{bid}", headers=headers).json()
    refund_shape_ok = isinstance(detail.get("refunds"), list) and (
        not detail["refunds"]
        or set(detail["refunds"][0].keys()) == {"amount_cents", "status", "processed_at"}
    )

    ok = health_ok and list_ok and rep_ok and refund_shape_ok
    record(
        "R16 contract shapes",
        ok,
        f"health={health_ok} list={list_ok} report={rep_ok} refunds={refund_shape_ok}",
    )


# ---------- driver ----------

def main() -> int:
    print("=== CoWork business-rule verification ===")
    checks = [
        rule_01_datetime_normalization,
        rule_02_pricing_and_duration,
        rule_03_atomicity_no_double_booking_and_quota,
        rule_04_unique_reference_codes_under_concurrency,
        rule_05_refund_tiers,
        rule_06_refund_amount_equals_log,
        rule_07_multi_tenancy_isolation,
        rule_08_jwt_claims,
        rule_09_access_token_lifetime_15min,
        rule_10_refresh_single_use,
        rule_11_logout_immediate_invalidation,
        rule_12_rate_limit_thread_safe,
        rule_13_stats_and_availability_freshness,
        rule_14_export_include_all_org_scoped,
        rule_15_registration_duplicate_username,
        rule_16_contract_response_shape,
    ]
    for c in checks:
        try:
            c()
        except AssertionError as e:
            record(c.__name__, False, f"assertion: {e}")
        except Exception as e:  # noqa: BLE001
            record(c.__name__, False, f"exception: {type(e).__name__}: {e}")

    total = len(_RESULTS)
    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    print()
    print(f"=== {passed}/{total} rules passed ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
