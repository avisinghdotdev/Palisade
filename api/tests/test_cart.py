from collections.abc import Iterator
from uuid import UUID, uuid4

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.shop.cart import Cart, Carts
from palisade_api.shop.models import CartRecord
from palisade_api.shop.router import carts as carts_dependency

pytestmark = pytest.mark.anyio


@pytest.fixture
def service(sessions: async_sessionmaker[AsyncSession]) -> Iterator[Carts]:
    carts = Carts(sessions)
    app.dependency_overrides[carts_dependency] = lambda: carts
    yield carts
    app.dependency_overrides.clear()


async def new_cart(client: httpx2.AsyncClient, wallet_id: UUID) -> Cart:
    response = await client.post("/api/shop/carts", json={"wallet_id": str(wallet_id)})
    assert response.status_code == 201
    return Cart.model_validate_json(response.content)


async def set_line(
    client: httpx2.AsyncClient, cart_id: UUID, size: str, quantity: int
) -> httpx2.Response:
    return await client.put(
        f"/api/shop/carts/{cart_id}/lines",
        json={"product_id": "nike-free-rn-flyknit", "size": size, "quantity": quantity},
    )


@pytest.mark.usefixtures("service")
async def test_a_new_cart_is_open_and_empty(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart = await new_cart(client, wallet_id)

    assert (cart.status, cart.lines, cart.total.value, cart.wallet_id) == (
        "open",
        [],
        "0.00",
        wallet_id,
    )
    fetched = await client.get(f"/api/shop/carts/{cart.id}")
    assert Cart.model_validate_json(fetched.content) == cart


@pytest.mark.usefixtures("service")
async def test_lines_are_set_by_product_and_size(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart = await new_cart(client, wallet_id)

    added = Cart.model_validate_json((await set_line(client, cart.id, "10", 1)).content)
    assert [(line.name, line.size, line.quantity) for line in added.lines] == [
        ("Free RN Flyknit", "10", 1)
    ]
    assert added.lines[0].unit_price.value == "120.00"

    more = Cart.model_validate_json((await set_line(client, cart.id, "10", 3)).content)
    assert [line.quantity for line in more.lines] == [3]
    assert more.total.value == "360.00"

    other = Cart.model_validate_json((await set_line(client, cart.id, "9", 1)).content)
    assert [(line.size, line.quantity) for line in other.lines] == [("10", 3), ("9", 1)]

    removed = Cart.model_validate_json(
        (await set_line(client, cart.id, "10", 0)).content
    )
    assert [line.size for line in removed.lines] == ["9"]
    assert removed.total.value == "120.00"


@pytest.mark.usefixtures("service")
async def test_bad_lines_are_refused(
    client: httpx2.AsyncClient, wallet_id: UUID
) -> None:
    cart = await new_cart(client, wallet_id)

    assert (await set_line(client, cart.id, "4", 1)).status_code == 422
    unknown = await client.put(
        f"/api/shop/carts/{cart.id}/lines",
        json={"product_id": "moon-boot", "size": "10", "quantity": 1},
    )
    assert unknown.status_code == 404
    assert (await set_line(client, uuid4(), "10", 1)).status_code == 404


async def test_a_cart_that_is_not_open_cannot_change(
    client: httpx2.AsyncClient,
    wallet_id: UUID,
    sessions: async_sessionmaker[AsyncSession],
    service: Carts,
) -> None:
    cart = await service.create(wallet_id)
    async with sessions.begin() as session:
        record = await session.get_one(CartRecord, cart.id)
        record.status = "completed"

    assert (await set_line(client, cart.id, "10", 1)).status_code == 409


@pytest.mark.usefixtures("service")
async def test_an_unknown_wallet_cannot_hold_a_cart(client: httpx2.AsyncClient) -> None:
    response = await client.post("/api/shop/carts", json={"wallet_id": str(uuid4())})

    assert response.status_code == 404
