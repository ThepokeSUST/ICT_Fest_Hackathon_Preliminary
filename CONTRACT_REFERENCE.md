# CoWork API — Mock Payloads for Every Route

Live server: `http://127.0.0.1:8000`. Run the live script with:
```
python -B mock_full.py
```

The mock flow below uses these accounts (registered by the script):

| User  | Org  | Role   | Password     |
|-------|------|--------|--------------|
| alice | OrgA | admin  | `pw-alice-1` |
| bob   | OrgA | member | `pw-bob-1`   |
| coa   | OrgA | admin  | `pw-coa-1`   |
| zoe   | OrgB | admin  | `pw-zoe-1`   |

All datetimes are UTC `Z`-suffixed.

---

## 0. `GET /health`
**Request** (no body, no auth)
**Response 200**
```json
{ "status": "ok" }
```

---

## 1. Auth

### 1a. `POST /auth/register` — admin of new org
**Body**
```json
{ "org_name": "OrgA", "username": "alice", "password": "pw-alice-1" }
```
**Response 201**
```json
{ "user_id": 1, "org_id": 1, "username": "alice", "role": "admin" }
```

### 1b. `POST /auth/register` — member joining existing org
**Body**
```json
{ "org_name": "OrgA", "username": "bob", "password": "pw-bob-1" }
```
**Response 201**
```json
{ "user_id": 2, "org_id": 1, "username": "bob", "role": "member" }
```

### 1c. `POST /auth/register` — duplicate username
**Response 409**
```json
{ "detail": "Username already taken", "code": "USERNAME_TAKEN" }
```

### 1d. `POST /auth/login` — success
**Body**
```json
{ "org_name": "OrgA", "username": "alice", "password": "pw-alice-1" }
```
**Response 200**
```json
{
  "access_token":  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type":    "bearer"
}
```

### 1e. `POST /auth/login` — wrong password
**Response 401**
```json
{ "detail": "Invalid credentials", "code": "INVALID_CREDENTIALS" }
```

### 1f. `POST /auth/refresh`
**Body**
```json
{ "refresh_token": "<eyJhbGci...>" }
```
**Response 200** — same shape as login (new pair, old refresh consumed)

### 1g. `POST /auth/refresh` — replay (reuse)
**Response 401**
```json
{ "detail": "Refresh token has been used", "code": "INVALID_CREDENTIALS" }
```

### 1h. `POST /auth/logout` — invalidates the presented access token
**Headers** `Authorization: Bearer <access_token>`
**Response 200** `{}` (empty body)

---

## 2. Rooms

### 2a. `POST /rooms` — admin creates a room
**Body**
```json
{ "name": "Conf A", "capacity": 8, "hourly_rate_cents": 1500 }
```
**Response 201**
```json
{ "id": 1, "org_id": 1, "name": "Conf A", "capacity": 8, "hourly_rate_cents": 1500 }
```

### 2b. `GET /rooms` — list caller's org rooms
**Response 200**
```json
[
  { "id": 1, "org_id": 1, "name": "Conf A",     "capacity": 8,  "hourly_rate_cents": 1500 },
  { "id": 2, "org_id": 1, "name": "Meeting B",  "capacity": 12, "hourly_rate_cents": 2000 },
  { "id": 3, "org_id": 1, "name": "Studio C",   "capacity": 4,  "hourly_rate_cents": 1200 }
]
```

### 2c. `POST /rooms` — member (non-admin) → 403
**Response 403**
```json
{ "detail": "Admin privileges required", "code": "FORBIDDEN" }
```

---

## 3. Bookings

### 3a. `POST /bookings` — create
**Body**
```json
{
  "room_id": 1,
  "start_time": "2026-07-12T10:00:00Z",
  "end_time":   "2026-07-12T12:00:00Z"
}
```
**Response 201**
```json
{
  "id": 1,
  "reference_code": "CW-3F2A9B7C1D",
  "room_id": 1,
  "user_id": 2,
  "start_time": "2026-07-12T10:00:00Z",
  "end_time":   "2026-07-12T12:00:00Z",
  "status": "confirmed",
  "price_cents": 3000,
  "created_at": "2026-07-09T19:55:00.123456Z"
}
```

### 3b. Overlap → 409 ROOM_CONFLICT
```json
{ "detail": "Room is already booked for that time", "code": "ROOM_CONFLICT" }
```

### 3c. `end_time <= start_time` → 400 INVALID_BOOKING_WINDOW
```json
{ "detail": "end_time must be after start_time", "code": "INVALID_BOOKING_WINDOW" }
```

### 3d. Duration > 8h → 400 INVALID_BOOKING_WINDOW
```json
{ "detail": "Duration must be between 1 and 8 hours", "code": "INVALID_BOOKING_WINDOW" }
```

### 3e. Start in the past → 400 INVALID_BOOKING_WINDOW
```json
{ "detail": "Start time must be in the future", "code": "INVALID_BOOKING_WINDOW" }
```

### 3f. 4th booking within 24h → 409 QUOTA_EXCEEDED
```json
{ "detail": "Quota of 3 bookings per 24 hours exceeded", "code": "QUOTA_EXCEEDED" }
```

### 3g. `GET /bookings?page=1&limit=10`
**Response 200**
```json
{
  "items": [ <Booking>, <Booking>, ... ],
  "page": 1,
  "limit": 10,
  "total": 4
}
```

### 3h. `GET /bookings/{id}`
**Response 200** — Booking object plus `refunds` array:
```json
{
  "id": 1,
  "reference_code": "CW-3F2A9B7C1D",
  "room_id": 1,
  "user_id": 2,
  "start_time": "2026-07-12T10:00:00Z",
  "end_time":   "2026-07-12T12:00:00Z",
  "status": "cancelled",
  "price_cents": 3000,
  "created_at": "2026-07-09T19:55:00.123456Z",
  "refunds": [
    { "amount_cents": 3000, "status": "processed", "processed_at": "2026-07-09T20:01:00.000000Z" }
  ]
}
```

### 3i. `GET /bookings/{id}` — other org's booking → 404
```json
{ "detail": "Booking not found", "code": "BOOKING_NOT_FOUND" }
```

---

## 4. Cancel + refunds

### 4a. `POST /bookings/{id}/cancel` — far future (≥ 48h)
**Response 200**
```json
{ "id": 4, "status": "cancelled", "refund_percent": 100, "refund_amount_cents": 3000 }
```

### 4b. `POST /bookings/{id}/cancel` — 24-48h
**Response 200**
```json
{ "id": 5, "status": "cancelled", "refund_percent": 50, "refund_amount_cents": 1500 }
```

### 4c. `POST /bookings/{id}/cancel` — < 24h
**Response 200**
```json
{ "id": 6, "status": "cancelled", "refund_percent": 0, "refund_amount_cents": 0 }
```

### 4d. Re-cancel → 409 ALREADY_CANCELLED
```json
{ "detail": "Booking already cancelled", "code": "ALREADY_CANCELLED" }
```

---

## 5. Availability / Stats

### 5a. `GET /rooms/{id}/availability?date=YYYY-MM-DD`
**Response 200**
```json
{
  "room_id": 1,
  "date": "2026-07-12",
  "busy": [
    { "start_time": "2026-07-12T10:00:00Z", "end_time": "2026-07-12T12:00:00Z" },
    { "start_time": "2026-07-12T12:00:00Z", "end_time": "2026-07-12T13:00:00Z" }
  ]
}
```

### 5b. `GET /rooms/{id}/stats`
**Response 200**
```json
{ "room_id": 1, "total_confirmed_bookings": 4, "total_revenue_cents": 9000 }
```

### 5c. `GET /rooms/{id}/stats` — other org's room → 404
```json
{ "detail": "Room not found", "code": "ROOM_NOT_FOUND" }
```

---

## 6. Admin

### 6a. `GET /admin/usage-report?from=YYYY-MM-DD&to=YYYY-MM-DD`
**Response 200**
```json
{
  "from": "2026-06-09",
  "to":   "2026-07-09",
  "rooms": [
    { "room_id": 1, "room_name": "Conf A",    "confirmed_bookings": 4, "revenue_cents": 9000 },
    { "room_id": 2, "room_name": "Meeting B", "confirmed_bookings": 1, "revenue_cents": 2000 },
    { "room_id": 3, "room_name": "Studio C",  "confirmed_bookings": 0, "revenue_cents": 0 }
  ]
}
```

### 6b. `GET /admin/usage-report` — non-admin → 403
```json
{ "detail": "Admin privileges required", "code": "FORBIDDEN" }
```

### 6c. `GET /admin/export` — CSV
**Response 200** (Content-Type: `text/csv`)
```
id,reference_code,room_id,user_id,start_time,end_time,status,price_cents
1,CW-3F2A9B7C1D,1,2,2026-07-12T10:00:00Z,2026-07-12T12:00:00Z,cancelled,3000
2,CW-7E0F1A2B3C,1,2,2026-07-12T12:00:00Z,2026-07-12T13:00:00Z,confirmed,2000
...
```
Optional query params: `room_id`, `include_all=true`. Always scoped to caller's org.

---

## 7. Auth failure modes (all return 401 with `code: "INVALID_CREDENTIALS"`)

| Scenario | Detail |
|---|---|
| No `Authorization` header | `"Missing bearer token"` |
| `Authorization: Bearer bogus` | `"Invalid or expired token"` |
| `Authorization: Bearer <refresh_token>` (wrong type) | `"Wrong token type"` |
| Revoked access token (after `/auth/logout`) | `"Token has been revoked"` |
| Unknown `sub` claim | `"Unknown user"` |

---

## 8. Rate limiting

After 20 `POST /bookings` from a single user within 60 seconds, the next one returns 429:
```json
{ "detail": "Too many requests", "code": "RATE_LIMITED" }
```

---

## 9. Concurrency safety

Two parallel `POST /bookings` for the same room+slot:
- Exactly **one** returns 201 (with a fresh `reference_code`).
- The other returns 409 `ROOM_CONFLICT` (verified by `tests/test_contract.py::test_concurrent_booking_no_double`).
- The `reference_code` is always unique (DB-enforced, retry on `IntegrityError`).
