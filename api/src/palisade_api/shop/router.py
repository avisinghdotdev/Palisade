from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from palisade_api.orders.checkout import (
    CartChanged,
    Checkout,
    CheckoutDeclined,
    CheckoutFailed,
    EmptyCart,
    WalletUnavailable,
)
from palisade_api.orders.store import Order
from palisade_api.shop.cart import (
    Cart,
    CartNotOpen,
    Carts,
    LineChange,
    UnknownCart,
    UnknownSize,
)
from palisade_api.shop.catalog import Catalog, Product, UnknownProduct
from palisade_api.wallets.service import UnknownWallet

router = APIRouter(prefix="/api/shop", tags=["shop"])


def catalog(request: Request) -> Catalog:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Catalog here.
    shop_catalog: Catalog = request.state.catalog  # pyright: ignore[reportAny]
    return shop_catalog


def carts(request: Request) -> Carts:
    service: Carts = request.state.carts  # pyright: ignore[reportAny]
    return service


def checkout(request: Request) -> Checkout:
    service: Checkout = request.state.checkout  # pyright: ignore[reportAny]
    return service


class CartWallet(BaseModel):
    wallet_id: UUID


@router.get("/products", operation_id="products")
async def products(shop: Annotated[Catalog, Depends(catalog)]) -> list[Product]:
    return await shop.products()


@router.get("/products/{product_id}", operation_id="product")
async def get_product(
    product_id: str, shop: Annotated[Catalog, Depends(catalog)]
) -> Product:
    try:
        return await shop.get(product_id)
    except UnknownProduct as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post("/carts", status_code=status.HTTP_201_CREATED, operation_id="create_cart")
async def create_cart(
    wallet: CartWallet, service: Annotated[Carts, Depends(carts)]
) -> Cart:
    """An empty cart that the given wallet will pay for."""
    try:
        return await service.create(wallet.wallet_id)
    except UnknownWallet as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get("/carts/{cart_id}", operation_id="cart")
async def get_cart(cart_id: UUID, service: Annotated[Carts, Depends(carts)]) -> Cart:
    try:
        return await service.get(cart_id)
    except UnknownCart as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.put("/carts/{cart_id}/lines", operation_id="set_cart_line")
async def set_cart_line(
    cart_id: UUID, change: LineChange, service: Annotated[Carts, Depends(carts)]
) -> Cart:
    """Set how many of a product in a size the cart holds; 0 removes the line."""
    try:
        return await service.set_line(cart_id, change)
    except (UnknownCart, UnknownProduct) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except UnknownSize as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)
        ) from error
    except CartNotOpen as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.put("/carts/{cart_id}/wallet", operation_id="set_cart_wallet")
async def set_cart_wallet(
    cart_id: UUID, wallet: CartWallet, service: Annotated[Carts, Depends(carts)]
) -> Cart:
    try:
        return await service.set_wallet(cart_id, wallet.wallet_id)
    except (UnknownCart, UnknownWallet) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except CartNotOpen as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.post("/carts/{cart_id}/checkout", operation_id="checkout")
async def place_order(
    cart_id: UUID, service: Annotated[Checkout, Depends(checkout)]
) -> Order:
    """Pay for the cart from its wallet; calling again returns the order already placed."""
    try:
        return await service.place_order(cart_id)
    except UnknownCart as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (CartNotOpen, EmptyCart, CartChanged, WalletUnavailable) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except CheckoutDeclined as error:
        raise HTTPException(status.HTTP_402_PAYMENT_REQUIRED, str(error)) from error
    except CheckoutFailed as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
