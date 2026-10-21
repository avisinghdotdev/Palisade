"""Products table with the Ridgeline Running catalog.

Revision ID: 44b07abe537d
Revises: 472d2e45fd62
Create Date: 2026-10-21 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "44b07abe537d"
down_revision: str | Sequence[str] | None = "472d2e45fd62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIZES = ["7", "7.5", "8", "8.5", "9", "9.5", "10", "10.5", "11", "11.5", "12", "13"]
UNSPLASH = "https://unsplash.com"

# Each photo is an Unsplash picture of the real shoe, used under the Unsplash License. The
# photographer and the photo page are shown on the shop's credits page.
CATALOG = [
    {
        "id": "nike-free-rn-flyknit",
        "brand": "Nike",
        "name": "Free RN Flyknit",
        "category": "Road",
        "description": "A flexible, sock-like trainer for short road runs.",
        "price_cents": 12000,
        "photo_by": "Ryan Waring",
        "photo_by_handle": "ryanwaring",
        "photo_page": "unpaired-red-nike-sneaker-164_6wVEHfI",
    },
    {
        "id": "nike-epic-react-flyknit-2",
        "brand": "Nike",
        "name": "Epic React Flyknit 2",
        "category": "Road",
        "description": "React foam and a knit upper for soft, springy daily miles.",
        "price_cents": 15000,
        "photo_by": "Izzy Gibson",
        "photo_by_handle": "rawimg",
        "photo_page": "white-and-red-nike-athletic-shoes-UC8F4jvYjjE",
    },
    {
        "id": "nike-lunarepic-low-flyknit-2",
        "brand": "Nike",
        "name": "LunarEpic Low Flyknit 2",
        "category": "Road",
        "description": "Laser-cut Lunarlon midsole with a low-cut knit collar.",
        "price_cents": 16000,
        "photo_by": "Brennan Burling",
        "photo_by_handle": "bwobble11",
        "photo_page": "unpaired-gray-and-white-nike-flyknit-shoe--fhcpPYjf3g",
    },
    {
        "id": "nike-lunarcharge-essential",
        "brand": "Nike",
        "name": "LunarCharge Essential",
        "category": "Road",
        "description": "Lightweight cushioning with a segmented outsole for easy days.",
        "price_cents": 11000,
        "photo_by": "Imani Bahati",
        "photo_by_handle": "imani_bht",
        "photo_page": "unpaired-gray-nike-running-shoe-LxVxPA1LOVM",
    },
    {
        "id": "hoka-mach-x-2",
        "brand": "Hoka",
        "name": "Mach X 2",
        "category": "Tempo",
        "description": "A plated tempo shoe with a supercritical foam top layer.",
        "price_cents": 19000,
        "photo_by": "Landon Liedtke",
        "photo_by_handle": "landonliedtke",
        "photo_page": "pair-of-white-hoka-running-shoes-with-lime-green-soles-853F1TIGVpk",
    },
    {
        "id": "adidas-ultraboost",
        "brand": "Adidas",
        "name": "Ultraboost Mid",
        "category": "Road",
        "description": "Boost midsole and a sock-fit Primeknit collar for long, cushioned runs.",
        "price_cents": 22000,
        "photo_by": "Will Suddreth",
        "photo_by_handle": "willsudds",
        "photo_page": "unpaired-gray-adidas-sneakers-1pjRN2kphIs",
    },
    {
        "id": "adidas-alphabounce",
        "brand": "Adidas",
        "name": "Alphabounce",
        "category": "Road",
        "description": "Bounce cushioning in a seamless Forgedmesh upper.",
        "price_cents": 10000,
        "photo_by": "Daniel Octavian",
        "photo_by_handle": "danieloctavian",
        "photo_page": "selective-focus-photography-of-unpaired-blue-and-white-adidas-running-shoe-leaning-on-concrete-pavement-at-daytime-nOeHuXPZKhk",
    },
    {
        "id": "brooks-hyperion-max",
        "brand": "Brooks",
        "name": "Hyperion Max",
        "category": "Tempo",
        "description": "A rockered racer-trainer for tempo days and long intervals.",
        "price_cents": 17000,
        "photo_by": "Lewis Hayden",
        "photo_by_handle": "lewdog9296",
        "photo_page": "a-pair-of-yellow-and-black-shoes-on-a-table-46NEqXYEGCg",
    },
    {
        "id": "new-balance-fresh-foam-roav",
        "brand": "New Balance",
        "name": "Fresh Foam Roav",
        "category": "Road",
        "description": "Fresh Foam cushioning in an everyday trainer that doubles as a walker.",
        "price_cents": 8000,
        "photo_by": "Archer Allstars",
        "photo_by_handle": "archerallstars",
        "photo_page": "a-pair-of-new-balance-shoes-on-a-wooden-floor-585bCxGPSGE",
    },
    {
        "id": "new-balance-fresh-foam-garoe",
        "brand": "New Balance",
        "name": "Fresh Foam Garoé",
        "category": "Trail",
        "description": "A mid-cut trail shoe with a toe cap and a lugged outsole.",
        "price_cents": 10000,
        "photo_by": "Archer Allstars",
        "photo_by_handle": "archerallstars",
        "photo_page": "a-pair-of-new-balance-shoes-on-a-wooden-floor-QUuW-OPJr-0",
    },
    {
        "id": "asics-gel-kayano-14",
        "brand": "Asics",
        "name": "Gel-Kayano 14",
        "category": "Stability",
        "description": "The 2008 stability trainer, reissued with its GEL heel and Duomax post.",
        "price_cents": 15000,
        "photo_by": "Vlad Ciutacu",
        "photo_by_handle": "vladieboi",
        "photo_page": "a-white-running-shoe-hovering-in-the-air-tR2ZoPR4OPc",
    },
    {
        "id": "asics-gel-kinsei-og",
        "brand": "Asics",
        "name": "Gel-Kinsei OG",
        "category": "Stability",
        "description": "The flagship from 2006 with visible GEL pods front and back.",
        "price_cents": 20000,
        "photo_by": "Hipkicks",
        "photo_by_handle": "hipkicks",
        "photo_page": "pair-of-white-and-purple-asics-sneakers-LdhTorI9T-o",
    },
]


def upgrade() -> None:
    products = op.create_table(
        "products",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("sizes", ARRAY(sa.Text()), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("photo_by", sa.Text(), nullable=False),
        sa.Column("photo_by_url", sa.Text(), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
    )
    op.bulk_insert(
        products,
        [
            {
                "id": item["id"],
                "brand": item["brand"],
                "name": item["name"],
                "category": item["category"],
                "description": item["description"],
                "price_cents": item["price_cents"],
                "sizes": SIZES,
                "image_path": f"/products/{item['id']}.jpg",
                "photo_by": item["photo_by"],
                "photo_by_url": f"{UNSPLASH}/@{item['photo_by_handle']}",
                "photo_url": f"{UNSPLASH}/photos/{item['photo_page']}",
            }
            for item in CATALOG
        ],
    )


def downgrade() -> None:
    op.drop_table("products")
