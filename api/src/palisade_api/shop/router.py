from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from palisade_api.shop.catalog import Catalog, Product, UnknownProduct

router = APIRouter(prefix="/api/shop", tags=["shop"])


def catalog(request: Request) -> Catalog:
    # Starlette types lifespan state as Any; the lifespan in main.py puts a Catalog here.
    shop_catalog: Catalog = request.state.catalog  # pyright: ignore[reportAny]
    return shop_catalog


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
