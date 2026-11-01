from collections.abc import Iterator
from uuid import UUID

import httpx2
import pytest
from dispute_fixtures import (
    DISPUTES_PATH,
    PaidOrder,
    new_dispute_id,
    paid_order,
    paypal_dispute,
)
from paypal_stub import PayPalStub, json_answer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.store import Activities
from palisade_api.disputes.service import Dispute, Disputes
from palisade_api.main import app
from palisade_api.paypal.auth import PayPalAuth
from palisade_api.paypal.client import PayPalClient
from palisade_api.sandbox.router import simulator as simulator_dependency
from palisade_api.sandbox.simulator import SEARCH_PATH, Simulator

pytestmark = pytest.mark.anyio


class Filed(BaseModel):
    disputed_transactions: list[dict[str, str]]
    reason: str
    dispute_amount: dict[str, str]


class Decided(BaseModel):
    adjudication_outcome: str


@pytest.fixture
def buyer() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def seller() -> PayPalStub:
    return PayPalStub()


@pytest.fixture
def sandbox(
    sessions: async_sessionmaker[AsyncSession], buyer: PayPalStub, seller: PayPalStub
) -> Iterator[None]:
    def client(stub: PayPalStub) -> PayPalClient:
        http = stub.client()
        return PayPalClient(http, PayPalAuth(http, "id", "secret"))

    seller_client = client(seller)
    activities = Activities(sessions)
    app.dependency_overrides[simulator_dependency] = lambda: Simulator(
        sessions,
        client(buyer),
        seller_client,
        Disputes(sessions, seller_client, activities),
        activities,
    )
    yield
    app.dependency_overrides.clear()


def search_row(transaction_id: str, code: str, value: str, invoice: str) -> object:
    return {
        "transaction_info": {
            "transaction_id": transaction_id,
            "transaction_event_code": code,
            "transaction_amount": {"currency_code": "USD", "value": value},
            "transaction_status": "S",
            "invoice_id": invoice,
        }
    }


def buyer_lists(buyer: PayPalStub, order: PaidOrder) -> None:
    """The demo buyer's search as PayPal returns it: a hold, the payment, and the hold's release."""
    buyer.on(
        "GET",
        SEARCH_PATH,
        json_answer(
            200,
            {
                "transaction_details": [
                    search_row("HOLD-1", "T1501", "-120.00", order.number),
                    search_row("BUYER-TXN-1", "T0003", "-120.00", order.number),
                    search_row("RELEASE-1", "T1105", "120.00", order.number),
                    search_row("OTHER-TXN", "T0003", "-80.00", "RR-OTHER1"),
                ]
            },
        ),
    )


@pytest.mark.usefixtures("sandbox")
async def test_the_simulator_files_as_the_demo_buyer_and_stores_the_dispute(
    sessions: async_sessionmaker[AsyncSession],
    buyer: PayPalStub,
    seller: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    buyer_lists(buyer, order)
    buyer.on(
        "POST",
        DISPUTES_PATH,
        json_answer(
            201,
            {
                "links": [
                    {
                        "rel": "self",
                        "href": f"https://api-m.sandbox.paypal.com{DISPUTES_PATH}/{dispute_id}",
                    }
                ]
            },
        ),
    )
    seller.on(
        "GET",
        f"{DISPUTES_PATH}/{dispute_id}",
        json_answer(200, paypal_dispute(dispute_id, order.capture_id)),
    )

    response = await client.post(
        f"/api/sandbox/orders/{order.number}/disputes", json={"reason": "OTHER"}
    )

    assert response.status_code == 201
    assert Dispute.model_validate_json(response.content).order_number == order.number
    [sent] = buyer.sent("POST", DISPUTES_PATH)
    filed = Filed.model_validate_json(sent.content)
    assert (filed.disputed_transactions, filed.reason, filed.dispute_amount) == (
        [{"buyer_transaction_id": "BUYER-TXN-1"}],
        "OTHER",
        {"value": "120.00", "currency_code": "USD"},
    )
    assert [row.kind for row in await Activities(sessions).for_dispute(dispute_id)] == [
        "sandbox_dispute_filed",
        "dispute_opened",
    ]


@pytest.mark.usefixtures("sandbox")
async def test_a_payment_the_search_does_not_list_yet_is_a_409(
    sessions: async_sessionmaker[AsyncSession],
    buyer: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    buyer.on("GET", SEARCH_PATH, json_answer(200, {"transaction_details": []}))

    response = await client.post(
        f"/api/sandbox/orders/{order.number}/disputes",
        json={"reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED"},
    )

    assert response.status_code == 409
    assert "three hours" in response.text
    assert buyer.sent("POST", DISPUTES_PATH) == []


@pytest.mark.usefixtures("sandbox")
async def test_another_buyers_order_is_not_disputed_here(
    sessions: async_sessionmaker[AsyncSession],
    buyer: PayPalStub,
    client: httpx2.AsyncClient,
    own_wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, own_wallet_id)

    response = await client.post(
        f"/api/sandbox/orders/{order.number}/disputes", json={"reason": "OTHER"}
    )

    assert response.status_code == 409
    assert buyer.requests == []


@pytest.mark.usefixtures("sandbox")
async def test_a_decision_waits_until_paypal_offers_one(
    sessions: async_sessionmaker[AsyncSession],
    seller: PayPalStub,
    client: httpx2.AsyncClient,
    wallet_id: UUID,
) -> None:
    order = await paid_order(sessions, wallet_id)
    dispute_id = new_dispute_id()
    detail = f"{DISPUTES_PATH}/{dispute_id}"
    decide = f"{detail}/adjudicate"
    seller.on(
        "GET", detail, json_answer(200, paypal_dispute(dispute_id, order.capture_id))
    )

    early = await client.post(
        f"/api/sandbox/disputes/{dispute_id}/adjudicate",
        json={"outcome": "SELLER_FAVOR"},
    )

    assert early.status_code == 409
    assert seller.sent("POST", decide) == []

    seller.on(
        "GET",
        detail,
        json_answer(
            200,
            paypal_dispute(
                dispute_id,
                order.capture_id,
                stage="CHARGEBACK",
                actions=("adjudicate",),
            ),
        ),
    )
    seller.on("POST", decide, json_answer(200, {"links": []}))

    decided = await client.post(
        f"/api/sandbox/disputes/{dispute_id}/adjudicate",
        json={"outcome": "SELLER_FAVOR"},
    )

    assert decided.status_code == 200
    [sent] = seller.sent("POST", decide)
    assert (
        Decided.model_validate_json(sent.content).adjudication_outcome == "SELLER_FAVOR"
    )
    assert [row.kind for row in await Activities(sessions).for_dispute(dispute_id)] == [
        "sandbox_dispute_adjudicated",
        "dispute_opened",
    ]
