"""The SQLAlchemy engine and the declarative base every feature's tables share."""

from typing import ClassVar

from sqlalchemy import URL, MetaData, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint names, so Alembic can find and drop them in later migrations.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata: ClassVar[MetaData] = MetaData(naming_convention=NAMING_CONVENTION)


def psycopg_url(database_url: str) -> URL:
    """The libpq-style `DATABASE_URL` with SQLAlchemy's psycopg 3 driver selected."""
    return make_url(database_url).set(drivername="postgresql+psycopg")


def create_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(
        psycopg_url(database_url), pool_size=5, pool_pre_ping=True
    )
