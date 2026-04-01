"""
Database engine & session factory.

SAFETY RULES:
  - create_all_tables() has been REMOVED. Schema changes go through Alembic only.
  - All mutations must happen inside explicit transactions (use db.begin() or rely
    on SessionLocal autocommit=False + explicit db.commit()).
  - pool_pre_ping=True ensures stale connections are recycled transparently.
  - pool_size / max_overflow tuned for a single-store POS (adjust for multi-store).
"""

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# pool_size / max_overflow are PostgreSQL-specific — SQLite (used in the test
# suite via pytest.ini DATABASE_URL=sqlite://) rejects them at engine creation.
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")

_engine_kwargs: dict = {"pool_pre_ping": True, "echo": settings.DEBUG}
if not _is_sqlite:
    _engine_kwargs["pool_size"]    = 10
    _engine_kwargs["max_overflow"] = 20

engine = create_engine(settings.DATABASE_URL, **_engine_kwargs)


@event.listens_for(engine, "connect")
def set_pg_session_defaults(dbapi_connection, connection_record):
    """Enforce sane PostgreSQL session settings on every new connection.

    Skipped for SQLite (e.g. test suite) -- SET commands are PostgreSQL syntax.
    """
    if _is_sqlite:
        return
    with dbapi_connection.cursor() as cursor:
        cursor.execute("SET statement_timeout = '30s'")
        cursor.execute("SET lock_timeout = '10s'")
        cursor.execute("SET TIME ZONE 'UTC'")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


# -- REMOVED: create_all_tables() ---------------------------------------------
# All schema changes must go through Alembic:
#   alembic revision --autogenerate -m "describe_change"
#   alembic upgrade head
# -----------------------------------------------------------------------------


def verify_db_connection() -> bool:
    """Called at startup to verify the DB is reachable."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection verified")
        return True
    except Exception as exc:
        logger.critical("Database connection failed: %s", exc)
        raise


def business_date():
    """
    Return today's date in the store's configured timezone (default Africa/Nairobi).

    Using date.today() returns the server's local date, which may be UTC and
    therefore wrong for late-night sales in East Africa. This helper ensures
    midnight-edge transactions land in the correct business day.
    """
    from datetime import datetime, timezone as _tz
    from app.core.config import settings
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(settings.STORE_TIMEZONE)
    except Exception:
        # Fallback: UTC (safe but may mis-classify midnight transactions)
        tz = _tz.utc
    return datetime.now(tz).date()
