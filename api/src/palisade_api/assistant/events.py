"""What the chat panel exchanges with the API: incoming messages, streamed events, stored turns."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from palisade_api.mandates.service import MandateKind, MandateStatus
from palisade_api.orders.store import OrderStatus
from palisade_api.shop.cart import CartStatus
from palisade_api.shop.catalog import Money

ToolName = Literal[
    "search_products",
    "get_product",
    "get_cart",
    "set_cart_line",
    "request_mandate",
    "check_mandate",
    "checkout",
    "get_order",
]
# What the shopper's side of a turn was: their words, or their answer on the consent page.
RequestKind = Literal[
    "message", "mandate_approved", "mandate_declined", "mandate_expired"
]
MESSAGE_LENGTH = 2000


class ProductCard(BaseModel):
    id: str
    brand: str
    name: str
    category: str
    price: Money
    image_path: str


class CartSummary(BaseModel):
    id: UUID
    status: CartStatus
    items: int
    total: Money


class MandateCard(BaseModel):
    id: UUID
    kind: MandateKind
    status: MandateStatus
    cap: Money
    # The consent page with the mandate's key, on the card of the request that issued it; a
    # card from a later check has none.
    consent_path: str | None = None
    expires_at: datetime


class OrderCard(BaseModel):
    number: str
    status: OrderStatus
    total: Money


class ProductsAttachment(BaseModel):
    type: Literal["products"]
    products: list[ProductCard]


class CartAttachment(BaseModel):
    type: Literal["cart"]
    cart: CartSummary


class MandateAttachment(BaseModel):
    type: Literal["mandate"]
    mandate: MandateCard


class OrderAttachment(BaseModel):
    type: Literal["order"]
    order: OrderCard


# What a tool result shows the shopper beside the agent's words.
Attachment = Annotated[
    ProductsAttachment | CartAttachment | MandateAttachment | OrderAttachment,
    Field(discriminator="type"),
]


class TextEvent(BaseModel):
    type: Literal["text"]
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"]
    id: str
    tool: ToolName
    input: dict[str, JsonValue]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"]
    id: str
    tool: ToolName
    ok: bool
    summary: str
    attachment: Attachment | None


# The agent's side of a turn, as it streams and as it is stored.
TurnEvent = Annotated[
    TextEvent | ToolCallEvent | ToolResultEvent, Field(discriminator="type")
]


class TurnRequest(BaseModel):
    kind: RequestKind
    text: str
    # The mandate a consent page answered, so the panel knows which request is settled.
    mandate_id: UUID | None = None


class Turn(BaseModel):
    position: int
    request: TurnRequest
    events: list[TurnEvent]
    created_at: datetime
    finished_at: datetime


class LiveTurn(BaseModel):
    """The turn still running, as far as it got: saved at each tool call, so any reader sees it."""

    request: TurnRequest
    events: list[TurnEvent]


class Conversation(BaseModel):
    id: UUID
    cart_id: UUID | None
    busy: bool
    turns: list[Turn]
    live: LiveTurn | None
    # The mandate the agent asked for and the shopper has not answered; the API carries the
    # conversation on by itself once the consent page answers it.
    awaiting: UUID | None
    created_at: datetime


class CreatedConversation(Conversation):
    """A new conversation with the key that opens it; the key is handed out this once."""

    key: str


class DoneEvent(BaseModel):
    """The turn as stored, the cart the agent is filling, and the mandate it now waits on."""

    type: Literal["done"]
    turn: Turn
    cart_id: UUID | None
    awaiting: UUID | None


class ErrorEvent(BaseModel):
    type: Literal["error"]
    detail: str


AssistantEvent = Annotated[
    TextEvent | ToolCallEvent | ToolResultEvent | DoneEvent | ErrorEvent,
    Field(discriminator="type"),
]


class ShopperMessage(BaseModel):
    text: str = Field(min_length=1, max_length=MESSAGE_LENGTH)
    # The browser's open cart, so the agent works on the bag the shopper sees now.
    cart_id: UUID | None = None


class NewConversation(BaseModel):
    wallet_id: UUID
    # The browser's open cart, so the agent fills the bag the shopper already sees.
    cart_id: UUID | None = None
