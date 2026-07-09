"""Database engine and session management."""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import DATABASE_URL

# SQLite: each request gets its own connection (single-writer model). The
# container runs a single uvicorn worker so a process-wide lock plus
# ``isolation_level=None`` (manual transactions) lets us use
# ``BEGIN IMMEDIATE`` for read-then-write regions without deadlocking.
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    isolation_level=None,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    # Enable foreign keys and WAL for better concurrency.
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
        except Exception:  # pragma: no cover
            pass
        cursor.close()
    except Exception:  # pragma: no cover - non-sqlite shim
        pass


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

Base = declarative_base()


def get_db():
    """Yield a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
