import asyncio
import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4, uuid7

import certifi
import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.database import create_engine
from palisade_api.main import app
from palisade_api.settings import DatabaseSettings
from palisade_api.wallets.models import WalletRecord

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


@pytest.fixture
async def wallet_id(sessions: async_sessionmaker[AsyncSession]) -> UUID:
    """A saved wallet inside the test's transaction, for carts and orders to use."""
    identifier = uuid7()
    async with sessions.begin() as session:
        session.add(
            WalletRecord(
                id=identifier,
                paypal_customer_id="customer_test",
                payment_token_id=f"PT-TEST-{uuid4().hex[:8]}",
                payer_id="PAYER-TEST",
                email="buyer@palisade.test",
                first_name="Test",
                demo=False,
                status="active",
            )
        )
    return identifier


@pytest.fixture
async def client() -> AsyncIterator[httpx2.AsyncClient]:
    """Requests straight into the app, no socket."""
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://palisade.test"
    ) as http:
        yield http
