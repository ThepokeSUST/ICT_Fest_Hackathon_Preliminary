"""CoWork API application entrypoint."""
from fastapi import FastAPI
from fastapi.security import HTTPBearer

from .database import Base, engine
from .errors import AppError, app_error_handler
from .routers import admin, auth, bookings, health, rooms

Base.metadata.create_all(bind=engine)

# Register an HTTPBearer security scheme so Swagger UI surfaces the green
# "Authorize" button. The actual bearer extraction is performed in
# ``app.auth.get_token_payload`` (the dependency below just exists for the
# OpenAPI surface; ``auto_error=False`` prevents FastAPI from intercepting
# requests — the manual handler in ``auth.py`` emits the documented
# ``{detail, code}`` error envelope).
bearer_scheme = HTTPBearer(bearerFormat="JWT", auto_error=False)

app = FastAPI(
    title="CoWork API",
    version="1.0.0",
    swagger_ui_init_oauth=None,
)

app.add_exception_handler(AppError, app_error_handler)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(rooms.router)
app.include_router(bookings.router)
app.include_router(admin.router)


# Attach the bearer scheme to the OpenAPI schema so Swagger's "Authorize"
# button appears. We do this by monkey-patching ``openapi()`` once at startup.
def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["HTTPBearer"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    schema["security"] = [{"HTTPBearer": []}]
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi
