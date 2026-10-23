import json
from collections.abc import Iterator
from uuid import uuid4

import httpx2
import pytest
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.wallets.router import wallets as wallets_dependency
from palisade_api.wallets.service import Wallet, Wallets

pytestmark = pytest.mark.anyio

PUBLIC_ORIGIN = "https://shop.test"
DEMO_EMAIL = "demo-buyer@palisade.test"


class SetupSource(BaseModel):
    usage_type: str
    experience_context: dict[str, str]


class SetupPaymentSource(BaseModel):
    paypal: SetupSource


class SetupBody(BaseModel):
    """The parts of the setup-token request the tests check."""

    payment_source: SetupPaymentSource


def setup_token(token_id: str) -> dict[str, object]:
    return {
        "id": token_id,
        "status": "PAYER_ACTION_REQUIRED",
        "links": [
            {"rel": "self", "href": f"https://api/v3/vault/setup-tokens/{token_id}"},
            {
                "rel": "approve",
                "href": f"https://sandbox.paypal.com/agreements/approve?approval_session_id={token_id}",
            },
        ],
    }


def payment_token(token_id: str, email: str) -> dict[str, object]:
    return {
        "id": token_id,
        "customer": {"id": "customer_1"},
        "payment_source": {
            "paypal": {
                "payer_id": "PAYER1",
                "email_address": email,
                "name": {
                    "given_name": "Maya",
                    "surname": "Okafor",
                    "full_name": "Maya Okafor",
                },
            }
        },
    }


@pytest.fixture
def paypal() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def service(
    sessions: async_sessionmaker[AsyncSession], paypal: PayPalStub
) -> Iterator[Wallets]:
    http = paypal.client()
    wallets = Wallets(
        sessions,
        PayPalClient(http, PayPalAuth(http, "client-id", "client-secret")),
        PUBLIC_ORIGIN,
        DEMO_EMAIL,
    )
    app.dependency_overrides[wallets_dependency] = lambda: wallets
    yield wallets
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("service")
async def test_starting_a_link_sends_the_buyer_to_paypal(
    paypal: PayPalStub, client: httpx2.AsyncClient
) -> None:
    paypal.on("POST", "/v3/vault/setup-tokens", json_answer(201, setup_token("ST-1")))

    response = await client.post("/api/shop/wallets/link")

    assert response.status_code == 200
    assert response.json() == {
        "setup_token_id": "ST-1",
        "approve_url": "https://sandbox.paypal.com/agreements/approve?approval_session_id=ST-1",
    }
    [request] = paypal.sent("POST", "/v3/vault/setup-tokens")
    assert request.headers["PayPal-Request-Id"]
    body = SetupBody.model_validate_json(request.content)
    assert body.payment_source.paypal.usage_type == "MERCHANT"
    assert body.payment_source.paypal.experience_context == {
        "brand_name": "Ridgeline Running",
        "shipping_preference": "NO_SHIPPING",
        "return_url": f"{PUBLIC_ORIGIN}/shop/wallet/return",
        "cancel_url": f"{PUBLIC_ORIGIN}/shop/wallet/cancel",
    }


@pytest.mark.usefixtures("service")
async def test_confirming_a_link_stores_the_wallet(
    paypal: PayPalStub, client: httpx2.AsyncClient
) -> None:
    token_id = f"PT-{uuid4().hex[:8]}"
    paypal.on(
        "POST",
        "/v3/vault/payment-tokens",
        json_answer(201, payment_token(token_id, "maya@palisade.test")),
    )

    response = await client.post(
        "/api/shop/wallets/confirm", json={"setup_token_id": "ST-1"}
    )

    assert response.status_code == 200
    wallet = Wallet.model_validate_json(response.content)
    assert (
        wallet.email,
        wallet.first_name,
        wallet.payer_id,
        wallet.demo,
        wallet.status,
    ) == (
        "maya@palisade.test",
        "Maya",
        "PAYER1",
        False,
        "active",
    )
    [request] = paypal.sent("POST", "/v3/vault/payment-tokens")
    assert json.loads(request.content) == {
        "payment_source": {"token": {"id": "ST-1", "type": "SETUP_TOKEN"}}
    }
    fetched = await client.get(f"/api/shop/wallets/{wallet.id}")
    assert fetched.status_code == 200
    assert Wallet.model_validate_json(fetched.content) == wallet


async def test_the_demo_buyer_becomes_the_default_wallet(
    service: Wallets, paypal: PayPalStub, client: httpx2.AsyncClient
) -> None:
    token_id = f"PT-{uuid4().hex[:8]}"
    paypal.on(
        "POST",
        "/v3/vault/payment-tokens",
        json_answer(201, payment_token(token_id, DEMO_EMAIL)),
    )

    linked = await service.confirm_link("ST-2")

    assert linked.demo is True
    response = await client.get("/api/shop/wallets/demo")
    assert response.status_code == 200
    assert Wallet.model_validate_json(response.content) == linked


async def test_a_repeated_link_refreshes_the_same_wallet(
    service: Wallets, paypal: PayPalStub
) -> None:
    token_id = f"PT-{uuid4().hex[:8]}"
    paypal.on(
        "POST",
        "/v3/vault/payment-tokens",
        json_answer(201, payment_token(token_id, "maya@palisade.test")),
    )

    first = await service.confirm_link("ST-1")
    await service.mark_deleted(token_id)
    second = await service.confirm_link("ST-3")

    assert second.id == first.id
    assert second.status == "active"


@pytest.mark.usefixtures("service")
async def test_an_unknown_wallet_is_a_404(client: httpx2.AsyncClient) -> None:
    response = await client.get(f"/api/shop/wallets/{uuid4()}")

    assert response.status_code == 404
