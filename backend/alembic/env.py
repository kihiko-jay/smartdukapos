"""
Alembic environment configuration — DukaPOS v4.0

Pulls DATABASE_URL from app.core.config.settings so credentials never live
in alembic.ini. Supports both online (live DB) and offline (SQL script) modes.
"""

import sys
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context

# Ensure the backend app is importable from the alembic/ subdirectory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import settings
from app.database import Base

# Import all models so Base.metadata is fully populated
import app.models.employee       # noqa: F401
import app.models.product        # noqa: F401
import app.models.transaction    # noqa: F401
import app.models.customer       # noqa: F401
import app.models.subscription   # noqa: F401
import app.models.audit          # noqa: F401

# Alembic Config object — gives access to values in alembic.ini
config = context.config

# Wire up Python logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata object Alembic uses to detect schema drift
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode — generates SQL script without
    connecting to the database. Useful for reviewing changes before applying.

    Usage:
        alembic upgrade head --sql > migration.sql
    """
    url = settings.DATABASE_URL
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — connects to the live database and
    applies changes directly.

    Usage:
        alembic upgrade head
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = settings.DATABASE_URL

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,   # NullPool is correct for migration scripts
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
