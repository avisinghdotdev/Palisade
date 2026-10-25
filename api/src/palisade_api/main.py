import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Literal, TypedDict

import httpx2
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures
from palisade_api.database import create_engine
from palisade_api.jobs import run_worker
from palisade_api.logging import RequestLogMiddleware
from palisade_api.mandates import router as mandates_router
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.migrate import upgrade_to_head
from palisade_api.orders import router as orders_router
from palisade_api.orders import webhooks as order_webhooks
from palisade_api.orders.calls import PayPalCalls
from palisade_api.orders.checkout import Checkout
from palisade_api.orders.repair import Repairs
from palisade_api.orders.shipping import Shipments
from palisade_api.orders.store import Orders
from palisade_api.paypal import router as paypal
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.policy.engine import Policies
from palisade_api.settings import Settings
from palisade_api.shop import router as shop
from palisade_api.shop.cart import Carts
from palisade_api.shop.catalog import Catalog
from palisade_api.wallets import router as wallets_router
from palisade_api.wallets import webhooks as wallet_webhooks
from palisade_api.wallets.service import Wallets
from palisade_api.webhooks import router as webhooks
from palisade_api.webhooks.store import WebhookStore
from palisade_api.webhooks.tasks import enqueue_processing
from palisade_api.webhooks.verification import WebhookVerifier

# A hung connection fails within this and is logged, instead of holding the caller.
READY_TIMEOUT_SECONDS = 2

logger = structlog.stdlib.get_logger(__name__)


class State(TypedDict):
    engine: AsyncEngine
    paypal_auth: PayPalAuth
    webhook_verifier: WebhookVerifier
    webhook_store: WebhookStore
    catalog: Catalog
    carts: Carts
    wallets: Wallets
    checkout: Checkout
    orders: Orders
    shipments: Shipments
    agent_signatures: AgentSignatures
    mandates: Mandates
    passkeys: Passkeys


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[State]:
    # pydantic-settings fills the fields from the environment; pyright only sees the synthesized constructor.
    settings = Settings()  # pyright: ignore[reportCallIssue]
    if settings.run_migrations:
        # Alembic's environment drives a sync engine, so it runs off the event loop.
        await asyncio.to_thread(upgrade_to_head)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    sessions = async_sessionmaker(engine)
    store = WebhookStore(sessions, enqueue_processing)
    agents = Agents(sessions)
    policies = Policies(sessions)
    try:
        # The registry and the policy follow the settings, so every process that starts with
        # the same seeds agrees on them; a changed seed is logged as a rotation.
        await agents.register(
            BuiltinAgent(
                "palisade-shopper",
                "Palisade Shopper",
                settings.shopper_agent_key.get_secret_value(),
            )
        )
        await agents.register(
            BuiltinAgent(
                "palisade-mcp",
                "Palisade MCP",
                settings.mcp_agent_key.get_secret_value(),
            )
        )
        await policies.ensure_default()
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    base_url=SANDBOX_BASE_URL, timeout=httpx2.Timeout(10.0)
                )
            )
            auth = PayPalAuth(
                http,
                settings.paypal_client_id,
                settings.paypal_client_secret.get_secret_value(),
            )
            paypal_client = PayPalClient(http, auth)
            wallets = Wallets(
                sessions,
                paypal_client,
                settings.public_origin,
                settings.demo_buyer_email,
            )
            orders = Orders(sessions)
            calls = PayPalCalls(sessions, paypal_client)
            passkeys = Passkeys(sessions, settings.public_origin)
            mandates = Mandates(
                sessions,
                MandateTokens(settings.mandate_issuer_key.get_secret_value()),
                passkeys,
                policies,
                settings.demo_signer_key.get_secret_value(),
                settings.public_origin,
            )
            if settings.run_worker:
                handlers = {
                    **order_webhooks.handlers(sessions),
                    **wallet_webhooks.handlers(wallets),
                }
                repairs = Repairs(
                    sessions,
                    Checkout(
                        sessions, calls, orders, policies, mandates, source="repair"
                    ),
                    paypal_client,
                )
                await stack.enter_async_context(
                    run_worker(
                        database_url,
                        {
                            "webhook_store": store,
                            "webhook_handlers": handlers,
                            "repairs": repairs,
                        },
                    )
                )
            yield State(
                engine=engine,
                paypal_auth=auth,
                webhook_verifier=WebhookVerifier(
                    paypal_client, settings.paypal_webhook_id
                ),
                webhook_store=store,
                catalog=Catalog(sessions),
                carts=Carts(sessions),
                wallets=wallets,
                checkout=Checkout(
                    sessions, calls, orders, policies, mandates, source="api"
                ),
                orders=orders,
                shipments=Shipments(sessions, calls, orders),
                agent_signatures=AgentSignatures(
                    sessions, agents, settings.public_origin
                ),
                mandates=mandates,
                passkeys=passkeys,
            )
    finally:
        await engine.dispose()


app = FastAPI(title="Palisade API", version=version("palisade-api"), lifespan=lifespan)
app.add_middleware(RequestLogMiddleware)
app.include_router(paypal.router)
app.include_router(webhooks.router)
app.include_router(shop.router)
app.include_router(wallets_router.router)
app.include_router(orders_router.router)
app.include_router(mandates_router.router)


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Readiness(BaseModel):
    status: Literal["ready"]


@app.get("/api/health", operation_id="health")
def health() -> Health:
    """What every platform probe calls: the process answers.

    It reads nothing, so a database outage neither restarts the replica nor takes it out of rotation.
    """
    return Health(status="ok", version=app.version)


def database(request: Request) -> AsyncEngine:
    # Starlette types lifespan state as Any; the lifespan above puts the engine here.
    engine: AsyncEngine = request.state.engine  # pyright: ignore[reportAny]
    return engine


@app.get(
    "/api/ready",
    operation_id="ready",
    responses={503: {"description": "The database did not answer in time."}},
)
async def ready(engine: Annotated[AsyncEngine, Depends(database)]) -> Readiness:
    """Whether this replica reaches its database; the release workflow checks it after a deploy."""
    try:
        async with (
            asyncio.timeout(READY_TIMEOUT_SECONDS),
            engine.connect() as connection,
        ):
            await connection.execute(text("SELECT 1"))
    except (DBAPIError, TimeoutError) as error:
        logger.warning("database_unreachable", exc_info=error)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "The database did not answer in time."
        ) from error
    return Readiness(status="ready")
