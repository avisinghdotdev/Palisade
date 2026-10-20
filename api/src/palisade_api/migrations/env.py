"""Alembic environment for the API's PostgreSQL database.

Migrations run on one connection that holds an advisory lock, so two replicas starting together
apply them once.
"""

from alembic import context
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from palisade_api.database import psycopg_url
from palisade_api.models import Base
from palisade_api.settings import DatabaseSettings

# Any constant every migrating process agrees on; pg_advisory_lock takes a bigint.
MIGRATION_LOCK = 7_391_002_118


def run_migrations() -> None:
    # pydantic-settings fills the field from the environment; pyright only sees the synthesized constructor.
    settings = DatabaseSettings()  # pyright: ignore[reportCallIssue]
    engine = create_engine(
        psycopg_url(settings.database_url.get_secret_value()), poolclass=NullPool
    )
    with engine.connect() as connection:
        connection.execute(
            text("select pg_advisory_lock(:lock)"), {"lock": MIGRATION_LOCK}
        )
        # The session-level lock outlives this commit; the migrations then get their own transaction.
        connection.commit()
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit(
        "Offline (--sql) migrations are not supported; run them against the database."
    )
run_migrations()
