from collections.abc import Iterator

import httpx2
import pytest
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.shop.catalog import Catalog, Product
from palisade_api.shop.router import catalog

pytestmark = pytest.mark.anyio

PRODUCTS = TypeAdapter(list[Product])


@pytest.fixture
def shop(sessions: async_sessionmaker[AsyncSession]) -> Iterator[Catalog]:
    shop_catalog = Catalog(sessions)
    app.dependency_overrides[catalog] = lambda: shop_catalog
    yield shop_catalog
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("shop")
async def test_the_catalog_lists_every_shoe_by_brand_and_name(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get("/api/shop/products")

    assert response.status_code == 200
    products = PRODUCTS.validate_json(response.content)
    keys = [(product.brand, product.name) for product in products]
    assert keys == sorted(keys)
    assert len(products) == 12
    assert {product.brand for product in products} == {
        "Adidas",
        "Asics",
        "Brooks",
        "Hoka",
        "New Balance",
        "Nike",
    }


@pytest.mark.usefixtures("shop")
async def test_a_product_carries_its_price_in_paypal_shape(
    client: httpx2.AsyncClient,
) -> None:
    response = await client.get("/api/shop/products/asics-gel-kayano-14")

    assert response.status_code == 200
    product = Product.model_validate_json(response.content)
    assert (product.brand, product.name, product.category) == (
        "Asics",
        "Gel-Kayano 14",
        "Stability",
    )
    assert product.price.model_dump() == {"value": "150.00", "currency_code": "USD"}
    assert "9.5" in product.sizes
    assert product.image_path == "/products/asics-gel-kayano-14.jpg"
    assert product.photo.model_dump() == {
        "by": "Vlad Ciutacu",
        "by_url": "https://unsplash.com/@vladieboi",
        "url": "https://unsplash.com/photos/a-white-running-shoe-hovering-in-the-air-tR2ZoPR4OPc",
    }


@pytest.mark.usefixtures("shop")
async def test_an_unknown_product_is_a_404(client: httpx2.AsyncClient) -> None:
    response = await client.get("/api/shop/products/moon-boot")

    assert response.status_code == 404
