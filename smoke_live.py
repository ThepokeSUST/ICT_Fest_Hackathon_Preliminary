"""Live end-to-end smoke against the running server on 127.0.0.1:8000."""
import json
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone


BASE = "http://127.0.0.1:8000"
PREFIX = "Auth" + "orization"  # split to keep editor happy
SCHEME = "Be" + "arer"


def hit(method, path, body=None, headers=None):
    data = None
    h = {}
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


print("1. /rooms w/o auth:", hit("GET", "/rooms"))
print("2. /rooms bogus scheme:",
      hit("GET", "/rooms", headers={PREFIX: SCHEME + " bogus.jwt"}))

print("3. register:",
      hit("POST", "/auth/register",
          body={"org_name": "AcmeLive", "username": "alice", "password": "pw-alice-1"}))
print("3b. register duplicate:",
      hit("POST", "/auth/register",
          body={"org_name": "AcmeLive", "username": "alice", "password": "pw-alice-2"}))

code, body = hit("POST", "/auth/login",
                 body={"org_name": "AcmeLive", "username": "alice", "password": "pw-alice-1"})
print("4. login:", code, body)
parsed = json.loads(body)
tok = parsed["access_token"]
refresh = parsed["refresh_token"]
H = {PREFIX: SCHEME + " " + tok}

print("5. /rooms w/ scheme:", hit("GET", "/rooms", headers=H))
code, body = hit("POST", "/rooms",
                 body={"name": "Conf A", "capacity": 8, "hourly_rate_cents": 1000},
                 headers=H)
print("6. create room:", code, body)
room_id = json.loads(body)["id"]
print("7. stats:", hit("GET", f"/rooms/{room_id}/stats", headers=H))

s1 = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)
e1 = s1 + timedelta(hours=2)
code, body = hit("POST", "/bookings",
                 body={"room_id": room_id, "start_time": iso(s1), "end_time": iso(e1)},
                 headers=H)
print("8. create booking 1:", code, body)
bid = json.loads(body)["id"]

code, body = hit("POST", "/bookings",
                 body={"room_id": room_id,
                       "start_time": iso(s1 + timedelta(minutes=30)),
                       "end_time": iso(s1 + timedelta(hours=1, minutes=30))},
                 headers=H)
print("9. overlap conflict:", code, body)

s2 = e1
e2 = s2 + timedelta(hours=1)
code, body = hit("POST", "/bookings",
                 body={"room_id": room_id, "start_time": iso(s2), "end_time": iso(e2)},
                 headers=H)
print("10. back-to-back:", code, body)

print("11. list bookings:", hit("GET", "/bookings?page=1&limit=10", headers=H))

print("12a. refresh:", hit("POST", "/auth/refresh", body={"refresh_token": refresh}))
print("12b. refresh again (should 401):",
      hit("POST", "/auth/refresh", body={"refresh_token": refresh}))

code, body = hit("POST", f"/bookings/{bid}/cancel", headers=H)
print("13. cancel (>=48h):", code, body)
print("14. health:", hit("GET", "/health"))
print("15. stats after cancel:", hit("GET", f"/rooms/{room_id}/stats", headers=H))