"""Saving a buyer's PayPal wallet through Vault v3, and the wallets already saved."""

from typing import Literal, final
from uuid import UUID, uuid4, uuid7

from pydantic import BaseModel, JsonValue
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.paypal.client import PayPalClient
from palisade_api.wallets.models import WalletRecord

WalletStatus = Literal["active", "deleted"]


class Wallet(BaseModel):
    id: UUID
    email: str
    first_name: str
    payer_id: str
    demo: bool
    status: WalletStatus


class LinkStart(BaseModel):
    """Where to send the buyer to approve the link, and the token to confirm with afterwards."""

    setup_token_id: str
    approve_url: str


class UnknownWallet(LookupError):
    def __init__(self, wallet_id: UUID) -> None:
        super().__init__(f"no wallet has the id {wallet_id}")


class NoDemoWallet(LookupError):
    def __init__(self) -> None:
        super().__init__("the demo buyer's wallet has not been linked")


class Link(BaseModel):
    rel: str
    href: str


class SetupToken(BaseModel):
    id: str
    links: list[Link]


class PayPalName(BaseModel):
    given_name: str


class PayPalWalletSource(BaseModel):
    payer_id: str
    email_address: str
    name: PayPalName


class PaymentSource(BaseModel):
    paypal: PayPalWalletSource


class Customer(BaseModel):
    id: str


class PaymentToken(BaseModel):
    id: str
    customer: Customer
    payment_source: PaymentSource


def wallet(record: WalletRecord) -> Wallet:
    return Wallet.model_validate(record, from_attributes=True)


@final
class Wallets:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        paypal: PayPalClient,
        public_origin: str,
        demo_buyer_email: str,
    ) -> None:
        self._sessions = sessions
        self._paypal = paypal
        self._public_origin = public_origin
        self._demo_buyer_email = demo_buyer_email

    async def start_link(self) -> LinkStart:
        """Create a setup token; the buyer approves it on PayPal's site."""
        body: dict[str, JsonValue] = {
            "payment_source": {
                "paypal": {
                    "usage_type": "MERCHANT",
                    "usage_pattern": "IMMEDIATE",
                    "customer_type": "CONSUMER",
                    "permit_multiple_payment_tokens": False,
                    "experience_context": {
                        "brand_name": "Ridgeline Running",
                        "shipping_preference": "NO_SHIPPING",
                        "return_url": f"{self._public_origin}/shop/wallet/return",
                        "cancel_url": f"{self._public_origin}/shop/wallet/cancel",
                    },
                }
            }
        }
        response = await self._paypal.post(
            "/v3/vault/setup-tokens", body, request_id=str(uuid4())
        )
        token = SetupToken.model_validate(response.body)
        approve = next(link.href for link in token.links if link.rel == "approve")
        return LinkStart(setup_token_id=token.id, approve_url=approve)

    async def confirm_link(self, setup_token_id: str) -> Wallet:
        """Exchange an approved setup token for the permanent payment token and store it."""
        response = await self._paypal.post(
            "/v3/vault/payment-tokens",
            {
                "payment_source": {
                    "token": {"id": setup_token_id, "type": "SETUP_TOKEN"}
                }
            },
            request_id=str(uuid4()),
        )
        token = PaymentToken.model_validate(response.body)
        source = token.payment_source.paypal
        values = {
            "paypal_customer_id": token.customer.id,
            "payer_id": source.payer_id,
            "email": source.email_address,
            "first_name": source.name.given_name,
            "demo": source.email_address == self._demo_buyer_email,
            "status": "active",
        }
        # PayPal hands back the same token for the same buyer and merchant, so a repeat link
        # refreshes the row instead of adding one.
        statement = (
            insert(WalletRecord)
            .values(id=uuid7(), payment_token_id=token.id, **values)
            .on_conflict_do_update(
                index_elements=[WalletRecord.payment_token_id], set_=values
            )
            .returning(WalletRecord)
        )
        async with self._sessions.begin() as session:
            record = (await session.execute(statement)).scalar_one()
            return wallet(record)

    async def demo(self) -> Wallet:
        statement = (
            select(WalletRecord)
            .where(WalletRecord.demo, WalletRecord.status == "active")
            .order_by(WalletRecord.created_at.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            record = await session.scalar(statement)
        if record is None:
            raise NoDemoWallet
        return wallet(record)

    async def get(self, wallet_id: UUID) -> Wallet:
        async with self._sessions() as session:
            record = await session.get(WalletRecord, wallet_id)
        if record is None:
            raise UnknownWallet(wallet_id)
        return wallet(record)

    async def mark_deleted(self, payment_token_id: str) -> bool:
        """Record that PayPal no longer holds the token; False when no wallet carried it."""
        statement = select(WalletRecord).where(
            WalletRecord.payment_token_id == payment_token_id
        )
        async with self._sessions.begin() as session:
            record = await session.scalar(statement)
            if record is None:
                return False
            record.status = "deleted"
            return True
