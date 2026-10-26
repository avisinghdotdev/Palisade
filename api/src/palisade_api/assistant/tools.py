"""The eight tools the in-page agent shops with, each a signed call to the store's own API.

Every call is shown to the shopper as it starts and as it ends, and lands in the activity log
with whatever it created. A refusal from the store comes back to Claude as an error that says
what went wrong and what to do next, never as an exception.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, final
from uuid import UUID, uuid4

from anthropic.lib.tools import BetaAsyncFunctionTool, ToolError
from pydantic import JsonValue, TypeAdapter

from palisade_api.activity.store import Activities
from palisade_api.agents.client import AgentHttp, ShopError
from palisade_api.assistant.events import (
    Attachment,
    CartAttachment,
    CartSummary,
    MandateAttachment,
    MandateCard,
    OrderAttachment,
    OrderCard,
    ProductCard,
    ProductsAttachment,
    ToolCallEvent,
    ToolName,
    ToolResultEvent,
    TurnEvent,
)
from palisade_api.assistant.model import Tool, ToolFunction
from palisade_api.mandates.service import Mandate
from palisade_api.orders.store import Order
from palisade_api.shop.cart import MAX_PER_LINE, Cart
from palisade_api.shop.catalog import Money, Product

PRODUCTS = TypeAdapter(list[Product])
PRODUCT = TypeAdapter(Product)
CART = TypeAdapter(Cart)
MANDATE = TypeAdapter(Mandate)
ORDER = TypeAdapter(Order)
# How many product cards a search shows the shopper; Claude gets every match.
CARDS_SHOWN = 6
# What Claude should do about a refusal, by the reason code the store gives.
NEXT_STEPS = {
    "MANDATE_UNKNOWN": "That is not a mandate id. Pass the id request_mandate or check_mandate returned.",
    "MANDATE_REQUIRED": "Call request_mandate first; nothing is paid before the shopper approves.",
    "MANDATE_PENDING": "The shopper has not approved yet. Ask them to approve on the consent page, then wait.",
    "MANDATE_DECLINED": "The shopper declined. Ask what they would like to change; do not retry.",
    "MANDATE_EXPIRED": "The approval ran out. Request a new mandate.",
    "MANDATE_USED": "That mandate already paid for an order. Request a new one for this purchase.",
    "MANDATE_CART_MISMATCH": "The cart changed after the shopper approved it. Request a new cart mandate.",
    "MANDATE_CAP_EXCEEDED": "The total is over what the shopper allowed. Remove something or ask for a higher cap.",
    "MANDATE_CATEGORY_NOT_ALLOWED": "The shopper did not allow that category. Remove those shoes or ask for a wider mandate.",
    "MANDATE_WALLET_MISMATCH": "The mandate was approved for another wallet. Request a new mandate.",
    "MANDATE_AGENT_MISMATCH": "The mandate belongs to another agent. Request a new mandate.",
    "POLICY_CHANGED": "The store's rules changed since the approval. Request a new mandate.",
    "ORDER_CAP_EXCEEDED": "The store caps what an agent may spend per order. Split the purchase or remove something.",
    "AGENT_DAILY_CAP_EXCEEDED": "The store's daily limit for agent purchases is reached. The shopper can still buy in the shop directly.",
    "AGENT_NOT_ALLOWED": "The store does not let this agent buy. The shopper can buy in the shop directly.",
    "CATEGORY_BLOCKED": "The store blocks agents from buying that category. Tell the shopper.",
}
# What the shopper reads when the store refuses, by reason code: plain words, no codes.
REFUSALS = {
    "MANDATE_UNKNOWN": "The store does not know that approval.",
    "MANDATE_REQUIRED": "The store needs your approval before anything is paid.",
    "MANDATE_PENDING": "The store is still waiting for your approval.",
    "MANDATE_DECLINED": "You declined this request.",
    "MANDATE_EXPIRED": "Your approval ran out.",
    "MANDATE_USED": "That approval already paid for an order.",
    "MANDATE_CART_MISMATCH": "The cart changed after you approved it.",
    "MANDATE_CAP_EXCEEDED": "The total is over what you allowed.",
    "MANDATE_CATEGORY_NOT_ALLOWED": "Your approval does not cover that category.",
    "MANDATE_WALLET_MISMATCH": "Your approval was for a different wallet.",
    "MANDATE_AGENT_MISMATCH": "Your approval was for a different agent.",
    "POLICY_CHANGED": "The store's rules changed after you approved.",
    "ORDER_CAP_EXCEEDED": "The store caps what an agent may spend on one order.",
    "AGENT_DAILY_CAP_EXCEEDED": "The store's daily limit for agent purchases is reached.",
    "AGENT_NOT_ALLOWED": "The store does not let this agent buy.",
    "CATEGORY_BLOCKED": "The store does not let agents buy that category.",
}

MandateKindInput = Literal["cart", "intent"]
Emit = Callable[[TurnEvent], None]
ParameterType = Literal["string", "integer", "array"]


@dataclass(frozen=True)
class Outcome:
    """What a tool hands back: words for Claude, a card and summary for the shopper, links for the log."""

    result: dict[str, JsonValue]
    summary: str
    attachment: Attachment | None = None
    mandate_id: UUID | None = None
    order_id: UUID | None = None


Handler = Callable[..., Awaitable[Outcome]]


@dataclass(frozen=True)
class Parameter:
    name: str
    type: ParameterType
    description: str


@dataclass(frozen=True)
class Spec:
    """How a tool is described to Claude; the words matter more than anything else about it."""

    name: ToolName
    description: str
    parameters: tuple[Parameter, ...]

    def schema(self) -> dict[str, object]:
        """A strict JSON schema: every parameter required, nothing extra allowed."""
        properties: dict[str, object] = {}
        for parameter in self.parameters:
            shape: dict[str, object] = {
                "type": parameter.type,
                "description": parameter.description,
            }
            if parameter.type == "array":
                shape["items"] = {"type": "string"}
            properties[parameter.name] = shape
        return {
            "type": "object",
            "properties": properties,
            "required": [parameter.name for parameter in self.parameters],
            "additionalProperties": False,
        }


SPECS: tuple[Spec, ...] = (
    Spec(
        "search_products",
        """Find shoes in the catalog. Searches the brand, name, category, and description for the words given and returns every match with its id, price, and sizes, best matches first; when nothing matches it returns the whole range so you can choose by hand. Use it before recommending anything. It does not return descriptions; call get_product for one shoe's details.""",
        (
            Parameter(
                "query",
                "string",
                "Words from the shopper's request, such as 'trail cushioned' or 'Hoka'. An empty string lists everything.",
            ),
            Parameter(
                "category",
                "string",
                "One of Road, Tempo, Trail, Stability to limit the search to that category, or an empty string for all categories.",
            ),
        ),
    ),
    Spec(
        "get_product",
        """One shoe in full: its description, price, and every US size offered. Use it when the shopper asks about a specific shoe or before recommending one you have not described yet. The id comes from search_products.""",
        (
            Parameter(
                "product_id", "string", "The product id, such as 'hoka-mach-x-2'."
            ),
        ),
    ),
    Spec(
        "get_cart",
        """What the shopper's cart holds right now: every line with its shoe, size, quantity, and price, and the total. It is the same cart the shopper fills in the shop, so call it when they mention their cart or bag, ask you to check out, or before a cart mandate for shoes you did not add yourself. An empty result means there is nothing to pay for yet.""",
        (),
    ),
    Spec(
        "set_cart_line",
        f"""Set how many pairs of one shoe in one size the shopper's cart holds; 0 removes the line. Creates the cart on the first call. Use it only after the shopper has confirmed the shoe and the size. Returns the whole cart with its total. The cart holds at most {MAX_PER_LINE} pairs of one shoe in one size.""",
        (
            Parameter("product_id", "string", "The product id from search_products."),
            Parameter(
                "size", "string", "The US size exactly as listed, such as '9.5'."
            ),
            Parameter(
                "quantity",
                "integer",
                f"How many pairs the line should hold, 0 to {MAX_PER_LINE}.",
            ),
        ),
    ),
    Spec(
        "request_mandate",
        """Ask the shopper to approve a purchase on the store's consent page. A 'cart' mandate covers the current cart exactly, once; the cart must hold something, and its cap is the cart total. An 'intent' mandate lets you buy without asking again, up to cap_usd in total, limited to the categories given, for 24 hours; use it only when the shopper asked to shop without approving each purchase. After calling it, tell the shopper to use the Approve button and wait; you are told when they answer.""",
        (
            Parameter("kind", "string", "'cart' or 'intent'."),
            Parameter(
                "cap_usd",
                "string",
                "For an intent mandate, the most the shopper allows in total, such as '300.00'. For a cart mandate pass an empty string; the cart total is used.",
            ),
            Parameter(
                "categories",
                "array",
                "For an intent mandate, the categories it allows (Road, Tempo, Trail, Stability); an empty list allows all. For a cart mandate pass an empty list.",
            ),
        ),
    ),
    Spec(
        "check_mandate",
        """The current state of a mandate: pending, signed, declined, or expired, with its cap and what it has paid so far. Use it when the shopper says they have approved or declined, or before a checkout under an intent mandate.""",
        (Parameter("mandate_id", "string", "The mandate id from request_mandate."),),
    ),
    Spec(
        "checkout",
        """Pay for the cart from the shopper's wallet under a signed mandate and place the order. Call it only after check_mandate reports 'signed' or you were told the shopper approved. The store checks the mandate and its policy first and refuses with a reason when either does not allow the purchase; the message says what to do next. Paying cannot be undone.""",
        (
            Parameter(
                "mandate_id",
                "string",
                "The id of the signed mandate that covers this purchase, as request_mandate or check_mandate returned it; never a cart id.",
            ),
        ),
    ),
    Spec(
        "get_order",
        """The state of an order placed earlier: paid, pending, or declined, with its total. Use it when the shopper asks about an order by its number.""",
        (
            Parameter(
                "order_number", "string", "The order number, such as 'RR-MQYD7P'."
            ),
        ),
    ),
)


def next_step(detail: str) -> str:
    """The store's refusal with the step Claude should take, when the reason code is a known one."""
    hints = [hint for code, hint in NEXT_STEPS.items() if code in detail]
    return f"{detail}. {hints[0]}" if hints else detail


def shopper_words(detail: str) -> str:
    """The store's refusal as the shopper reads it: each reason in plain words, no codes."""
    reasons = [words for code, words in REFUSALS.items() if code in detail]
    return " ".join(reasons) if reasons else detail


def usd(amount: str) -> Money:
    """A dollar amount Claude typed, as the store's two-decimal money.

    Raises:
        ToolError: the text is not a positive dollar amount.
    """
    try:
        value = Decimal(amount.strip().removeprefix("$").replace(",", ""))
    except InvalidOperation as error:
        raise ToolError(
            f"{amount!r} is not a dollar amount; pass digits like 300.00"
        ) from error
    if value <= 0:
        raise ToolError("the cap must be more than zero dollars")
    return Money(value=f"{value:.2f}", currency_code="USD")


def product_card(product: Product) -> ProductCard:
    return ProductCard.model_validate(product, from_attributes=True)


def listing(product: Product) -> dict[str, JsonValue]:
    return {
        "id": product.id,
        "brand": product.brand,
        "name": product.name,
        "category": product.category,
        "price_usd": product.price.value,
        "sizes": list(product.sizes),
    }


def cart_summary(cart: Cart) -> CartSummary:
    return CartSummary(
        id=cart.id,
        status=cart.status,
        items=sum(line.quantity for line in cart.lines),
        total=cart.total,
    )


def cart_result(cart: Cart) -> dict[str, JsonValue]:
    return {
        "cart_id": str(cart.id),
        "status": cart.status,
        "lines": [
            {
                "product_id": line.product_id,
                "name": f"{line.brand} {line.name}",
                "size": line.size,
                "quantity": line.quantity,
                "line_total_usd": line.line_total.value,
            }
            for line in cart.lines
        ],
        "total_usd": cart.total.value,
    }


def mandate_card(mandate: Mandate) -> MandateCard:
    return MandateCard.model_validate(mandate, from_attributes=True)


def mandate_result(mandate: Mandate) -> dict[str, JsonValue]:
    return {
        "mandate_id": str(mandate.id),
        "kind": mandate.kind,
        "status": mandate.status,
        "cap_usd": mandate.cap.value,
        "spent_usd": mandate.spent.value,
        "categories": list(mandate.categories),
        "expires_at": mandate.expires_at.isoformat(),
        "consent_path": mandate.consent_path,
    }


def order_card(order: Order) -> OrderCard:
    return OrderCard.model_validate(order, from_attributes=True)


def order_result(order: Order) -> dict[str, JsonValue]:
    return {
        "order_number": order.number,
        "status": order.status,
        "total_usd": order.total.value,
        "receipt_path": f"/shop/orders/{order.number}",
        "paypal_order_id": order.paypal_order_id,
        "capture_id": order.capture_id,
    }


def score(product: Product, words: list[str]) -> int:
    text = f"{product.brand} {product.name} {product.category} {product.description}"
    return sum(word in text.lower() for word in words)


@final
class Shopping:
    """One shopper's shopping through the store's API: its cart, and where events and the log go.

    The chat gives it a conversation and a panel to show each call in; the MCP endpoint gives it
    neither, and only the log sees its calls.
    """

    def __init__(
        self,
        shop: AgentHttp,
        activities: Activities,
        agent_id: str,
        conversation_id: UUID | None,
        wallet_id: UUID,
        cart_id: UUID | None,
        emit: Emit,
    ) -> None:
        self._shop = shop
        self._activities = activities
        self._agent_id = agent_id
        self._conversation_id = conversation_id
        self._wallet_id = wallet_id
        self.cart_id: UUID | None = cart_id
        self._emit = emit
        self._handlers: dict[ToolName, Handler] = {
            "search_products": self.search_products,
            "get_product": self.get_product,
            "get_cart": self.get_cart,
            "set_cart_line": self.set_cart_line,
            "request_mandate": self.request_mandate,
            "check_mandate": self.check_mandate,
            "checkout": self.checkout,
            "get_order": self.get_order,
        }

    def tools(self) -> list[Tool]:
        return [self._tool(spec) for spec in SPECS]

    async def call(self, name: ToolName, **arguments: JsonValue) -> Outcome:
        """Run one tool the way the shopper and the log see it: shown as it starts and ends, logged.

        Raises:
            ToolError: the store refused or the call was wrong; the message says what to do next.
        """
        call_id = uuid4().hex[:12]
        self._emit(
            ToolCallEvent(type="tool_call", id=call_id, tool=name, input=arguments)
        )
        try:
            outcome = await self._handlers[name](**arguments)
        except ShopError as error:
            told = next_step(error.detail)
            await self._failed(
                call_id, name, arguments, shopper_words(error.detail), told
            )
            raise ToolError(told) from error
        except ToolError as error:
            await self._failed(call_id, name, arguments, str(error), str(error))
            raise
        self._emit(
            ToolResultEvent(
                type="tool_result",
                id=call_id,
                tool=name,
                ok=True,
                summary=outcome.summary,
                attachment=outcome.attachment,
            )
        )
        await self._activities.record(
            "assistant_tool_call",
            agent_id=self._agent_id,
            conversation_id=self._conversation_id,
            mandate_id=outcome.mandate_id,
            order_id=outcome.order_id,
            tool=name,
            input=arguments,
            ok=True,
            summary=outcome.summary,
        )
        return outcome

    async def search_products(self, query: str, category: str) -> Outcome:
        products = PRODUCTS.validate_python(await self._shop.get("/api/shop/products"))
        wanted = category.strip().lower()
        candidates = [
            product
            for product in products
            if not wanted or product.category.lower() == wanted
        ]
        words = query.lower().split()
        ranked = sorted(
            ((score(product, words), product) for product in candidates),
            key=lambda pair: -pair[0],
        )
        # A query that names one shoe ("Nike Free RN Flyknit") must not drag in every Nike.
        best = ranked[0][0] if ranked else 0
        floor = len(words) if best == len(words) else 1
        hits = [product for points, product in ranked if points >= floor]
        result: dict[str, JsonValue] = {}
        if not hits:
            hits = candidates
            result["note"] = (
                "Nothing matched those words; here is everything in range instead."
                if words
                else "The whole range."
            )
        result["products"] = [listing(product) for product in hits]
        return Outcome(
            result=result,
            summary=f"{len(hits)} {'shoe' if len(hits) == 1 else 'shoes'} found",
            attachment=ProductsAttachment(
                type="products",
                products=[product_card(product) for product in hits[:CARDS_SHOWN]],
            ),
        )

    async def get_product(self, product_id: str) -> Outcome:
        product = PRODUCT.validate_python(
            await self._shop.get(f"/api/shop/products/{product_id}")
        )
        return Outcome(
            result={**listing(product), "description": product.description},
            summary=f"{product.brand} {product.name}",
            attachment=ProductsAttachment(
                type="products", products=[product_card(product)]
            ),
        )

    async def get_cart(self) -> Outcome:
        cart = await self._current_cart()
        if cart is None:
            return Outcome(
                result={
                    "lines": [],
                    "total_usd": "0.00",
                    "note": "The shopper has no open cart; set_cart_line starts one.",
                },
                summary="No cart yet",
            )
        return self._cart_outcome(cart)

    async def set_cart_line(self, product_id: str, size: str, quantity: int) -> Outcome:
        cart_id = await self._open_cart()
        cart = CART.validate_python(
            await self._shop.put(
                f"/api/shop/carts/{cart_id}/lines",
                {"product_id": product_id, "size": size, "quantity": quantity},
            )
        )
        return self._cart_outcome(cart)

    async def request_mandate(
        self, kind: MandateKindInput, cap_usd: str, categories: list[str]
    ) -> Outcome:
        body: dict[str, JsonValue] = {
            "kind": kind,
            "wallet_id": str(self._wallet_id),
            "categories": [] if kind == "cart" else list(categories),
        }
        if kind == "cart":
            cart = await self._filled_cart()
            body["cap"] = cart.total.model_dump()
            body["cart_id"] = str(cart.id)
        else:
            body["cap"] = usd(cap_usd).model_dump()
        mandate = MANDATE.validate_python(await self._shop.post("/api/mandates", body))
        return Outcome(
            result={
                **mandate_result(mandate),
                "next_step": "The shopper now sees an Approve button for this request in the chat. Ask them to approve it there, then wait; you are told when they answer.",
            },
            summary="Waiting for the shopper's approval",
            attachment=MandateAttachment(type="mandate", mandate=mandate_card(mandate)),
            mandate_id=mandate.id,
        )

    async def check_mandate(self, mandate_id: str) -> Outcome:
        mandate = MANDATE.validate_python(
            await self._shop.get(f"/api/mandates/{mandate_id}")
        )
        return Outcome(
            result=mandate_result(mandate),
            summary=f"Mandate {mandate.status}",
            attachment=MandateAttachment(type="mandate", mandate=mandate_card(mandate)),
            mandate_id=mandate.id,
        )

    async def checkout(self, mandate_id: str) -> Outcome:
        if self.cart_id is None:
            raise ToolError("There is no cart to pay for; add shoes first.")
        order = ORDER.validate_python(
            await self._shop.post(
                f"/api/shop/carts/{self.cart_id}/checkout", {"mandate_id": mandate_id}
            )
        )
        # The cart is spent; the next pair the shopper wants starts a new one.
        self.cart_id = None
        return self._order_outcome(order)

    async def get_order(self, order_number: str) -> Outcome:
        order = ORDER.validate_python(
            await self._shop.get(f"/api/orders/{order_number}")
        )
        return self._order_outcome(order)

    @staticmethod
    def _cart_outcome(cart: Cart) -> Outcome:
        items = sum(line.quantity for line in cart.lines)
        pairs = "pair" if items == 1 else "pairs"
        return Outcome(
            result=cart_result(cart),
            summary=f"Cart: {items} {pairs}, ${cart.total.value}",
            attachment=CartAttachment(type="cart", cart=cart_summary(cart)),
        )

    @staticmethod
    def _order_outcome(order: Order) -> Outcome:
        return Outcome(
            result=order_result(order),
            summary=f"Order {order.number} {order.status}",
            attachment=OrderAttachment(type="order", order=order_card(order)),
            mandate_id=None if order.mandate is None else order.mandate.id,
            order_id=order.id,
        )

    async def _current_cart(self) -> Cart | None:
        """The conversation's cart while it is open; None when there is none or it was paid for."""
        if self.cart_id is None:
            return None
        try:
            cart = CART.validate_python(
                await self._shop.get(f"/api/shop/carts/{self.cart_id}")
            )
        except ShopError as error:
            if error.status_code != 404:
                raise
            return None
        return cart if cart.status == "open" else None

    async def _filled_cart(self) -> Cart:
        """The conversation's open cart with lines in it.

        Raises:
            ToolError: there is no such cart.
        """
        cart = await self._current_cart()
        if cart is None or not cart.lines:
            raise ToolError(
                "The cart is empty; add shoes before asking for a cart mandate."
            )
        return cart

    async def _open_cart(self) -> UUID:
        """The conversation's open cart, or a new one when there is none or it was checked out."""
        cart = await self._current_cart()
        if cart is not None:
            return cart.id
        cart = CART.validate_python(
            await self._shop.post(
                "/api/shop/carts", {"wallet_id": str(self._wallet_id)}
            )
        )
        self.cart_id = cart.id
        return cart.id

    def _tool(self, spec: Spec) -> Tool:
        """The spec as Claude's runner calls it: the model's arguments in, the result as text out."""

        async def run(**arguments: JsonValue) -> str:
            return json.dumps((await self.call(spec.name, **arguments)).result)

        runner: ToolFunction = run
        return BetaAsyncFunctionTool[ToolFunction](
            runner,
            name=spec.name,
            description=spec.description,
            input_schema=spec.schema(),
            strict=True,
        )

    async def _failed(
        self,
        call_id: str,
        name: ToolName,
        arguments: dict[str, JsonValue],
        shown: str,
        told: str,
    ) -> None:
        """Show the shopper why the call failed, in their words, and log that with Claude's."""
        self._emit(
            ToolResultEvent(
                type="tool_result",
                id=call_id,
                tool=name,
                ok=False,
                summary=shown,
                attachment=None,
            )
        )
        await self._activities.record(
            "assistant_tool_call",
            agent_id=self._agent_id,
            conversation_id=self._conversation_id,
            tool=name,
            input=arguments,
            ok=False,
            summary=shown,
            told=told,
        )
