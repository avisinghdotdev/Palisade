"""Alembic environment for the API's PostgreSQL database.

Migrations run on one connection that holds an advisory lock, so two replicas starting together
apply them once. A database already migrated by newer code is left alone: the shared database is
ahead of a running build whenever a newer one has started, and such a build must keep running.
"""

import structlog
from alembic import context
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from palisade_api.database import psycopg_url
from palisade_api.models import Base
from palisade_api.settings import DatabaseSettings

# Any constant every migrating process agrees on; pg_advisory_lock takes a bigint.
MIGRATION_LOCK = 7_391_002_118

# Alembic loads this file under its own module name, so the logger is named by hand.
logger = structlog.stdlib.get_logger("palisade_api.migrations")


def run_migrations() -> None:
    # pydantic-settings fills the field from the environment; pyright only sees the synthesized constructor.
    settings = DatabaseSettings()  # pyright: ignore[reportCallIssue]
    engine = create_engine(
        psycopg_url(settings.database_url.get_secret_value()), poolclass=NullPool
    )
    known = {
        revision.revision
        for revision in ScriptDirectory.from_config(context.config).walk_revisions()
    }
    with engine.connect() as connection:
        connection.execute(
            text("select pg_advisory_lock(:lock)"), {"lock": MIGRATION_LOCK}
        )
        # The session-level lock outlives this commit; the migrations then get their own transaction.
        connection.commit()
        context.configure(connection=connection, target_metadata=Base.metadata)
        unknown = set(context.get_context().get_current_heads()) - known
        if unknown:
            logger.warning("database_ahead_of_code", revisions=sorted(unknown))
            return
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit(
        "Offline (--sql) migrations are not supported; run them against the database."
    )
run_migrations()
