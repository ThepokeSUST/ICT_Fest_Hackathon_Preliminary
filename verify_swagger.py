"""Verify the OpenAPI schema advertises the bearer scheme and the
endpoints still return 401 with the documented error envelope when no
header is supplied."""
import json

from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)

# 1. OpenAPI schema: must include a securitySchemes block with HTTPBearer.
schema = c.get("/openapi.json").json()
components = schema.get("components", {})
schemes = components.get("securitySchemes", {})
print("security schemes:", list(schemes.keys()))
print("HTTPBearer block:", json.dumps(schemes.get("HTTPBearer"), indent=2))
print("top-level security:", schema.get("security"))

# 2. /rooms without header must still return 401 INVALID_CREDENTIALS.
r = c.get("/rooms")
print("/rooms w/o header:", r.status_code, r.text)

# 3. /rooms with header from a real login works.
c.post("/auth/register",
       json={"org_name": "SwaggerOrg", "username": "demo", "password": "pw-demo-1"})
r = c.post("/auth/login",
           json={"org_name": "SwaggerOrg", "username": "demo", "password": "pw-demo-1"})
tok = r.json()["access_token"]
r = c.get("/rooms", headers={"Authorization": "Bearer " + tok})
print("/rooms w/ header :", r.status_code, r.text)