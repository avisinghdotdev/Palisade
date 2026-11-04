import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version
from typing import Annotated, Literal, TypedDict

import httpx2
import structlog
from anthropic import AsyncAnthropic
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.types import ASGIApp

from palisade_api.activity import router as activity_router
from palisade_api.activity.store import Activities
from palisade_api.agents import router as agents_router
from palisade_api.agents.client import AgentHttp, AgentSigner
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.agents.signatures import AgentSignatures
from palisade_api.assistant import router as assistant_router
from palisade_api.assistant.access import ChatAccess
from palisade_api.assistant.local import LocalApp
from palisade_api.assistant.model import ClaudeModel
from palisade_api.assistant.prompt import SYSTEM_PROMPT
from palisade_api.assistant.service import Assistant
from palisade_api.database import create_engine
from palisade_api.disputes import router as disputes_router
from palisade_api.disputes import webhooks as dispute_webhooks
from palisade_api.disputes.alerts import Alerts
from palisade_api.disputes.narrative import ClaudeNarrator
from palisade_api.disputes.responses import Responses
from palisade_api.disputes.service import Disputes
from palisade_api.disputes.tasks import cancel_refund, enqueue_response, schedule_refund
from palisade_api.jobs import run_worker
from palisade_api.logging import RequestLogMiddleware
from palisade_api.maintenance import Housekeeping
from palisade_api.mandates import router as mandates_router
from palisade_api.mandates.passkeys import Passkeys
from palisade_api.mandates.service import Mandates
from palisade_api.mandates.tokens import MandateTokens
from palisade_api.mcp.server import MOUNT, MountedServer, asgi, build
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
from palisade_api.policy import router as policy_router
from palisade_api.policy.engine import Policies
from palisade_api.sandbox import router as sandbox_router
from palisade_api.sandbox.simulator import Simulator
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
# How long a stopping replica waits for running chat turns, inside the platform's 30 s grace.
TURN_GRACE_SECONDS = 20
SHOPPER_AGENT = "palisade-shopper"
MCP_AGENT = "palisade-mcp"

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
    disputes: Disputes
    responses: Responses
    alerts: Alerts
    policies: Policies
    agents: Agents
    simulator: Simulator
    agent_signatures: AgentSignatures
    mandates: Mandates
    passkeys: Passkeys
    activities: Activities
    chat_access: ChatAccess
    assistant: Assistant
    mcp_app: ASGIApp


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[State]:
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
    activities = Activities(sessions)
    try:
        # The registry and the policy follow the settings, so every process that starts with
        # the same seeds agrees on them; a changed seed is logged as a rotation.
        await agents.register(
            BuiltinAgent(
                SHOPPER_AGENT,
                "Ridgeline Assistant",
                settings.shopper_agent_key.get_secret_value(),
            )
        )
        await agents.register(
            BuiltinAgent(
                MCP_AGENT, "MCP Agent", settings.mcp_agent_key.get_secret_value()
            )
        )
        await policies.ensure_current()
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
            buyer_paypal = PayPalClient(
                http,
                PayPalAuth(
                    http,
                    settings.paypal_buyer_client_id,
                    settings.paypal_buyer_client_secret.get_secret_value(),
                ),
            )
            claude = await stack.enter_async_context(
                AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
            )
            disputes = Disputes(sessions, paypal_client, activities)
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
                activities,
                settings.demo_signer_key.get_secret_value(),
                settings.public_origin,
            )
            responses = Responses(
                sessions,
                calls,
                disputes,
                policies,
                activities,
                ClaudeNarrator(claude),
                enqueue_response,
            )
            alerts = Alerts(
                sessions,
                calls,
                disputes,
                policies,
                activities,
                schedule_refund,
                cancel_refund,
            )
            # Every change PayPal reports is a cue to answer the dispute, and for an alert to
            # start or stop its refund timer.
            disputes.on_change(responses.schedule)
            disputes.on_change(alerts.schedule)
            if settings.run_worker:
                handlers = {
                    **order_webhooks.handlers(sessions),
                    **wallet_webhooks.handlers(wallets),
                    **dispute_webhooks.handlers(disputes),
                }
                repairs = Repairs(
                    sessions,
                    Checkout(
                        sessions,
                        calls,
                        orders,
                        policies,
                        mandates,
                        activities,
                        source="repair",
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
                            "housekeeping": Housekeeping(sessions),
                            "disputes": disputes,
                            "responses": responses,
                            "alerts": alerts,
                        },
                    )
                )
            # The agent's tools call this same app in-process; they find the state below in
            # their scope, filled in once it exists.
            local_state: dict[str, object] = {}
            shop_http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(
                        app=LocalApp(app, local_state), raise_app_exceptions=False
                    ),
                    base_url=settings.public_origin,
                    timeout=httpx2.Timeout(60.0),
                )
            )
            mcp = build(
                AgentHttp(
                    shop_http,
                    AgentSigner(MCP_AGENT, settings.mcp_agent_key.get_secret_value()),
                ),
                activities,
                wallets,
                MCP_AGENT,
                settings.public_origin,
            )
            mcp_app = asgi(mcp)
            await stack.enter_async_context(mcp.session_manager.run())
            assistant = Assistant(
                sessions,
                ClaudeModel(claude, SYSTEM_PROMPT),
                AgentHttp(
                    shop_http,
                    AgentSigner(
                        SHOPPER_AGENT, settings.shopper_agent_key.get_secret_value()
                    ),
                ),
                mandates,
                activities,
                SHOPPER_AGENT,
            )
            # An answer on the consent page carries the chat that asked for it on.
            mandates.on_answer(assistant.continue_after)
            state = State(
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
                    sessions,
                    calls,
                    orders,
                    policies,
                    mandates,
                    activities,
                    source="api",
                ),
                orders=orders,
                shipments=Shipments(sessions, calls, orders),
                disputes=disputes,
                responses=responses,
                alerts=alerts,
                policies=policies,
                agents=agents,
                simulator=Simulator(
                    sessions, buyer_paypal, paypal_client, disputes, activities
                ),
                agent_signatures=AgentSignatures(
                    sessions, agents, activities, settings.public_origin
                ),
                mandates=mandates,
                passkeys=passkeys,
                activities=activities,
                chat_access=ChatAccess(settings.chat_access_key.get_secret_value()),
                assistant=assistant,
                mcp_app=mcp_app,
            )
            local_state.update(state)
            yield state
            # Turns run on after their responses; a stopping replica lets them end first.
            try:
                async with asyncio.timeout(TURN_GRACE_SECONDS):
                    await assistant.idle()
            except TimeoutError:
                logger.warning("assistant_turns_cut_short")
    finally:
        await engine.dispose()


app = FastAPI(title="Palisade API", version=version("palisade-api"), lifespan=lifespan)
app.add_middleware(RequestLogMiddleware)
app.include_router(paypal.router)
app.include_router(webhooks.router)
app.include_router(shop.router)
app.include_router(wallets_router.router)
app.include_router(orders_router.router)
app.include_router(disputes_router.router)
app.include_router(policy_router.router)
app.include_router(agents_router.router)
app.include_router(sandbox_router.router)
app.include_router(mandates_router.router)
app.include_router(activity_router.router)
app.include_router(assistant_router.router)
# After the routers, so the shop's own paths win and only the MCP endpoint falls through.
app.mount(MOUNT, MountedServer(), name="mcp")


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
