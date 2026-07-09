"""Verify the live response shape for the 401 unauthorized case."""
import json
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)

# Health
r = c.get("/health")
print("health:", r.status_code, r.text)

# /rooms w/o scheme
r = c.get("/rooms")
print("/rooms w/o scheme:", r.status_code, r.text)

# /rooms w/ bogus scheme
r = c.get("/rooms", headers={"Authorization": "Bearer bogus"})
print("/rooms bogus scheme:", r.status_code, r.text)

# Register
r = c.post("/auth/register",
           json={"org_name": "VerifyOrg", "username": "bob", "password": "pw-bob-1"})
print("register:", r.status_code, r.text)

# Login
r = c.post("/auth/login",
           json={"org_name": "VerifyOrg", "username": "bob", "password": "pw-bob-1"})
print("login:", r.status_code, r.text)
tok = r.json()["access_token"]

# /rooms w/ valid scheme
r = c.get("/rooms", headers={"Authorization": f"Bearer {tok}"})
print("/rooms w/ valid:", r.status_code, r.text)