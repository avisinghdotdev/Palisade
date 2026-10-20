import asyncio
import os
from collections.abc import AsyncIterator

import certifi
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.database import create_engine
from palisade_api.settings import DatabaseSettings

# psycopg's bundled OpenSSL finds no system trust store on Windows; `sslrootcert=system` then
# falls back to this file.
os.environ["SSL_CERT_FILE"] = certifi.where()


@pytest.fixture
def anyio_backend() -> tuple[str, dict[str, object]]:
    # psycopg's async mode cannot run on the proactor event loop, Windows' default.
    return ("asyncio", {"loop_factory": asyncio.SelectorEventLoop})


@pytest.fixture
async def sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions on one connection whose transaction is rolled back after the test.

    The database is shared with the deployed app, so nothing a test writes may survive it; the
    sessions' own commits only release savepoints inside that transaction.
    """
    # pydantic-settings fills the field from the environment; pyright only sees the synthesized constructor.
    settings = DatabaseSettings()  # pyright: ignore[reportCallIssue]
    engine = create_engine(settings.database_url.get_secret_value())
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield async_sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint"
        )
        await transaction.rollback()
    await engine.dispose()
