from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Literal, TypedDict

import httpx2
from fastapi import FastAPI
from pydantic import BaseModel

from palisade_api.paypal import router as paypal
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.settings import Settings
from palisade_api.webhooks import router as webhooks
from palisade_api.webhooks.store import WebhookStore
from palisade_api.webhooks.verification import WebhookVerifier


class State(TypedDict):
    paypal_auth: PayPalAuth
    webhook_verifier: WebhookVerifier
    webhook_store: WebhookStore


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[State]:
    # pydantic-settings fills the fields from the environment; pyright only sees the synthesized constructor.
    settings = Settings()  # pyright: ignore[reportCallIssue]
    async with httpx2.AsyncClient(
        base_url=SANDBOX_BASE_URL, timeout=httpx2.Timeout(10.0)
    ) as http:
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
            webhook_store=WebhookStore(),
        )


app = FastAPI(title="Palisade API", version=version("palisade-api"), lifespan=lifespan)
app.include_router(paypal.router)
app.include_router(webhooks.router)


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@app.get("/api/health", operation_id="health")
def health() -> Health:
    return Health(status="ok", version=app.version)
