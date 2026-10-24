"""PayPal answers and carts the checkout, repair, and shipping tests share."""

from uuid import UUID

import httpx2

from palisade_api.shop.cart import Cart

CREATE_PATH = "/v2/checkout/orders"
CAPTURE_PATH = "/v2/payments/authorizations/AUTH-1/capture"
VOID_PATH = "/v2/payments/authorizations/AUTH-1/void"
ORDER_PATH = "/v2/checkout/orders/PP-ORDER-1"


def created_order(expiration_time: str = "2026-11-21T10:00:00Z") -> dict[str, object]:
    return {
        "id": "PP-ORDER-1",
        "status": "COMPLETED",
        "payer": {"email_address": "buyer@palisade.test"},
        "purchase_units": [
            {
                "payments": {
                    "authorizations": [
                        {
                            "id": "AUTH-1",
                            "status": "CREATED",
                            "expiration_time": expiration_time,
                        }
                    ]
                }
            }
        ],
    }


def declined() -> dict[str, object]:
    return {
        "name": "UNPROCESSABLE_ENTITY",
        "message": "The requested action could not be performed.",
        "debug_id": "debug-declined",
        "details": [
            {
                "issue": "INSTRUMENT_DECLINED",
                "description": "The instrument was declined.",
            }
        ],
    }


async def cart_with_shoes(client: httpx2.AsyncClient, wallet_id: UUID) -> Cart:
    response = await client.post("/api/shop/carts", json={"wallet_id": str(wallet_id)})
    cart = Cart.model_validate_json(response.content)
    await client.put(
        f"/api/shop/carts/{cart.id}/lines",
        json={"product_id": "nike-free-rn-flyknit", "size": "10", "quantity": 1},
    )
    await client.put(
        f"/api/shop/carts/{cart.id}/lines",
        json={"product_id": "hoka-mach-x-2", "size": "9.5", "quantity": 2},
    )
    return cart


async def cart_status(client: httpx2.AsyncClient, cart_id: UUID) -> str:
    return Cart.model_validate_json(
        (await client.get(f"/api/shop/carts/{cart_id}")).content
    ).status
