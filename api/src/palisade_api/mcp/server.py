"""The store's MCP endpoint: the chat's shopping tools for any agent that speaks MCP.

The endpoint keeps no session, so any replica answers any request: a cart id travels with the
calls that need one, the shopper approves on the consent page whose link request_mandate
returns, and every call reaches the store signed as a registered agent, under the same policy
and mandate checks as the in-page chat.
"""

from typing import Annotated, Literal, final
from uuid import UUID

from anthropic.lib.tools import ToolError as Refusal
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field, JsonValue
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from palisade_api.activity.store import Activities
from palisade_api.agents.client import AgentHttp
from palisade_api.assistant.events import ToolName
from palisade_api.assistant.tools import Outcome, Shopping
from palisade_api.wallets.service import NoDemoWallet, Wallets

# The API mounts the server at MOUNT; the endpoint any client connects to is MOUNT + ENDPOINT.
MOUNT = "/api/shop"
ENDPOINT = "/mcp"

INSTRUCTIONS = """Ridgeline Running sells running shoes from Nike, Adidas, Hoka, Brooks, New Balance, and Asics. Palisade, the trust layer the store uses, verifies you and keeps the shopper's signed approval with each order. Shop in this order: search_products; set_cart_line for each shoe the shopper confirmed (the first call creates the cart and returns its cart_id; pass that id to every later cart call); request_mandate, which returns a consent_url the shopper must open in their browser and approve; check_mandate until it reports signed; then checkout with that mandate_id. Nothing is paid before the shopper approves, and a checkout cannot be undone. A cart mandate covers the cart as it is, once. An intent mandate lets you buy without asking again, up to its cap, for 24 hours; use it only when the shopper asked for that. The demo buyer's PayPal sandbox wallet pays, with test money."""

READS = ToolAnnotations(read_only_hint=True, open_world_hint=False)
CHANGES = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
PAYS = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)

CartId = Annotated[
    str,
    Field(
        description="The cart_id a cart call returned; empty when there is no cart yet."
    ),
]
MandateId = Annotated[
    str, Field(description="The mandate_id request_mandate or check_mandate returned.")
]
ProductId = Annotated[str, Field(description="The product id from search_products.")]


def cart(cart_id: str) -> UUID | None:
    """The cart a call names, or None for no cart yet.

    Raises:
        ToolError: the id is not one a cart call returned.
    """
    if cart_id == "":
        return None
    try:
        return UUID(cart_id)
    except ValueError as error:
        raise ToolError("cart_id must be the cart_id a cart call returned") from error


def linked(
    result: dict[str, JsonValue], origin: str, path: str, url: str
) -> dict[str, JsonValue]:
    """The result with the page at `path` spelled out as a full link under `url`."""
    return {**result, url: f"{origin}{result[path]}"}


@final
class ShopTools:
    """The chat's tools as any agent calls them: each call signed by `shop`, logged under `agent_id`."""

    def __init__(
        self,
        shop: AgentHttp,
        activities: Activities,
        wallets: Wallets,
        agent_id: str,
        origin: str,
    ) -> None:
        self._shop = shop
        self._activities = activities
        self._wallets = wallets
        self._agent_id = agent_id
        self._origin = origin

    async def search_products(
        self,
        query: Annotated[
            str,
            Field(
                description="Words from the shopper's request, such as 'trail cushioned' or 'Hoka'; empty lists everything."
            ),
        ],
        category: Annotated[
            str,
            Field(
                description="Road, Tempo, Trail, or Stability to limit the search; empty for all."
            ),
        ] = "",
    ) -> dict[str, JsonValue]:
        """Find shoes in the catalog, best matches first, each with its id, price, and sizes; nothing matching returns the whole range. Call it before recommending anything."""
        return (
            await self._call("search_products", "", query=query, category=category)
        ).result

    async def get_product(self, product_id: ProductId) -> dict[str, JsonValue]:
        """One shoe in full: its description, price, and every US size offered."""
        return (await self._call("get_product", "", product_id=product_id)).result

    async def get_cart(self, cart_id: CartId = "") -> dict[str, JsonValue]:
        """What the cart holds right now: every line with its shoe, size, quantity, and price, and the total."""
        return (await self._call("get_cart", cart_id)).result

    async def set_cart_line(
        self,
        product_id: ProductId,
        size: Annotated[
            str, Field(description="The US size as listed, such as '9.5'.")
        ],
        quantity: Annotated[
            int,
            Field(ge=0, le=5, description="Pairs the line should hold; 0 removes it."),
        ],
        cart_id: CartId = "",
    ) -> dict[str, JsonValue]:
        """Set how many pairs of one shoe in one size the cart holds, creating the cart when there is none; returns the whole cart with its cart_id and total. Use it only after the shopper confirmed the shoe and the size."""
        return (
            await self._call(
                "set_cart_line",
                cart_id,
                product_id=product_id,
                size=size,
                quantity=quantity,
            )
        ).result

    async def request_mandate(
        self,
        kind: Annotated[
            Literal["cart", "intent"],
            Field(
                description="'cart' covers the cart as it is, once; 'intent' allows purchases up to cap_usd for 24 hours."
            ),
        ],
        cart_id: CartId = "",
        cap_usd: Annotated[
            str,
            Field(
                description="For an intent mandate, the most the shopper allows in total, such as '300.00'; empty for a cart mandate."
            ),
        ] = "",
        categories: Annotated[
            tuple[str, ...],
            Field(
                description="For an intent mandate, the categories it allows (Road, Tempo, Trail, Stability); empty allows all."
            ),
        ] = (),
    ) -> dict[str, JsonValue]:
        """Ask the shopper to approve a purchase on the store's consent page. Give the shopper the consent_url to open in their browser, then call check_mandate until it reports signed. A cart mandate needs a cart with lines in it."""
        outcome = await self._call(
            "request_mandate",
            cart_id,
            kind=kind,
            cap_usd=cap_usd,
            categories=list(categories),
        )
        return {
            **linked(outcome.result, self._origin, "consent_path", "consent_url"),
            "next_step": "Give the shopper the consent_url to open in their browser. Call check_mandate until it reports signed, then checkout.",
        }

    async def check_mandate(self, mandate_id: MandateId) -> dict[str, JsonValue]:
        """The state of a mandate: pending, signed, declined, or expired, with its cap and what it has paid so far."""
        return (await self._call("check_mandate", "", mandate_id=mandate_id)).result

    async def checkout(
        self, cart_id: CartId, mandate_id: MandateId
    ) -> dict[str, JsonValue]:
        """Pay for the cart from the shopper's wallet under a signed mandate and place the order. The store checks the mandate and its policy first and refuses with a reason when either does not allow it. Paying cannot be undone."""
        outcome = await self._call("checkout", cart_id, mandate_id=mandate_id)
        return linked(outcome.result, self._origin, "receipt_path", "receipt_url")

    async def get_order(
        self,
        order_number: Annotated[
            str, Field(description="The order number, such as 'RR-MQYD7P'.")
        ],
    ) -> dict[str, JsonValue]:
        """The state of an order placed earlier: paid, pending, or declined, with its total and receipt."""
        outcome = await self._call("get_order", "", order_number=order_number)
        return linked(outcome.result, self._origin, "receipt_path", "receipt_url")

    async def _call(
        self, name: ToolName, cart_id: str, **arguments: JsonValue
    ) -> Outcome:
        try:
            wallet = await self._wallets.demo()
        except NoDemoWallet as error:
            raise ToolError(str(error)) from error
        shopping = Shopping(
            self._shop,
            self._activities,
            self._agent_id,
            None,
            wallet.id,
            cart(cart_id),
            lambda _: None,
        )
        try:
            return await shopping.call(name, **arguments)
        except Refusal as error:
            raise ToolError(str(error)) from error


def build(
    shop: AgentHttp,
    activities: Activities,
    wallets: Wallets,
    agent_id: str,
    origin: str,
) -> MCPServer:
    """The server with the chat's eight tools, in the order the chat lists them."""
    tools = ShopTools(shop, activities, wallets, agent_id, origin)
    server = MCPServer(
        "Ridgeline Running", instructions=INSTRUCTIONS, website_url=origin
    )
    server.add_tool(
        tools.search_products, title="Search the catalog", annotations=READS
    )
    server.add_tool(tools.get_product, title="Read a shoe's details", annotations=READS)
    server.add_tool(tools.get_cart, title="Read the cart", annotations=READS)
    server.add_tool(tools.set_cart_line, title="Change the cart", annotations=CHANGES)
    server.add_tool(
        tools.request_mandate, title="Ask the shopper to approve", annotations=CHANGES
    )
    server.add_tool(
        tools.check_mandate, title="Check the shopper's answer", annotations=READS
    )
    server.add_tool(tools.checkout, title="Pay with PayPal", annotations=PAYS)
    server.add_tool(tools.get_order, title="Check an order", annotations=READS)
    return server


def asgi(server: MCPServer) -> Starlette:
    """The server as the app mounted at MOUNT: no session to pin a client to a replica, plain JSON replies.

    Cloudflare and the platform's ingress own the Host header and the endpoint is public, so the
    SDK's localhost-only guard against DNS rebinding is off.
    """
    return server.streamable_http_app(
        streamable_http_path=ENDPOINT,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )


@final
class MountedServer:
    """Serves the MCP app the lifespan builds, from the path mounted before it existed."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Starlette types the lifespan state as Any; the lifespan puts the app here.
        app: ASGIApp = scope["state"]["mcp_app"]  # pyright: ignore[reportAny]
        await app(scope, receive, send)
