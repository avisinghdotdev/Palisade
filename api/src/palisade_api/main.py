from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Literal, TypedDict

import httpx2
from fastapi import FastAPI
from pydantic import BaseModel

from palisade_api.paypal import router as paypal
from palisade_api.paypal.auth import SANDBOX_BASE_URL, PayPalAuth
from palisade_api.settings import Settings


class State(TypedDict):
    paypal_auth: PayPalAuth


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[State]:
    # pydantic-settings fills the fields from the environment; pyright only sees the synthesized constructor.
    settings = Settings()  # pyright: ignore[reportCallIssue]
    async with httpx2.AsyncClient(
        base_url=SANDBOX_BASE_URL, timeout=httpx2.Timeout(10.0)
    ) as http:
        yield State(
            paypal_auth=PayPalAuth(
                http,
                settings.paypal_client_id,
                settings.paypal_client_secret.get_secret_value(),
            )
        )


app = FastAPI(title="Palisade API", version=version("palisade-api"), lifespan=lifespan)
app.include_router(paypal.router)


class Health(BaseModel):
    status: Literal["ok"]
    version: str


@app.get("/api/health", operation_id="health")
def health() -> Health:
    return Health(status="ok", version=app.version)
