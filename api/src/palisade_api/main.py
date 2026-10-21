import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from importlib.metadata import version
from typing import Literal, TypedDict

import httpx2
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import async_sessionmaker

from palisade_api.database import create_engine
from palisade_api.jobs import run_worker
from palisade_api.logging import RequestLogMiddleware, configure_logging
from palisade_api.migrate import upgrade_to_head
from palisade_api.paypal import router as paypal
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.settings import Settings
from palisade_api.webhooks import router as webhooks
from palisade_api.webhooks.store import WebhookStore
from palisade_api.webhooks.tasks import enqueue_processing
from palisade_api.webhooks.verification import WebhookVerifier


class State(TypedDict):
    paypal_auth: PayPalAuth
    webhook_verifier: WebhookVerifier
    webhook_store: WebhookStore


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[State]:
    # pydantic-settings fills the fields from the environment; pyright only sees the synthesized constructor.
    settings = Settings()  # pyright: ignore[reportCallIssue]
    configure_logging(settings.log_level, settings.log_format)
    if settings.run_migrations:
        # Alembic's environment drives a sync engine, so it runs off the event loop.
        await asyncio.to_thread(upgrade_to_head)
    database_url = settings.database_url.get_secret_value()
    engine = create_engine(database_url)
    store = WebhookStore(async_sessionmaker(engine), enqueue_processing)
    try:
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(
                httpx2.AsyncClient(
                    base_url=SANDBOX_BASE_URL, timeout=httpx2.Timeout(10.0)
                )
            )
            if settings.run_worker:
                await stack.enter_async_context(
                    run_worker(database_url, {"webhook_store": store})
                )
            auth = PayPalAuth(
                http,
                settings.paypal_client_id,
                settings.paypal_client_secret.get_secret_value(),
            )
            yield State(
                paypal_auth=auth,
                webhook_verifier=WebhookVerifier(
                    PayPalClient(http, auth), settings.paypal_webhook_id
                ),
                webhook_store=store,
            )
    finally:
        await engine.dispose()


app = FastAPI(title="Palisade API", version=version("palisade-api"), lifespan=lifespan)
app.add_middleware(RequestLogMiddleware)
app.include_router(paypal.router)
app.include_router(webhooks.router)


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@app.get("/api/health", operation_id="health")
def health() -> Health:
    return Health(status="ok", version=app.version)
