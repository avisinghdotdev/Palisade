"""The merchant's policy for agent purchases, and the check every agent checkout passes."""

import hashlib
import json
from datetime import timedelta
from typing import final
from uuid import uuid7

import structlog
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.orders.models import OrderRecord
from palisade_api.policy.models import PolicyRecord
from palisade_api.shop.models import CartRecord

# An agent's purchases in this window count against its daily cap.
DAILY_WINDOW = timedelta(hours=24)
# Order statuses that may hold or took the buyer's money: a create still waiting on PayPal
# counts, so a second checkout running beside it cannot slip under a cap.
SPENDING_STATUSES = ("creating", "authorized", "capture_pending", "captured")
# The merchant's rules. Both caps sit far above any sandbox purchase, so no shopper is refused
# on spend; the rules stay for a merchant that sets real ones.
MERCHANT_RULES = {
    "allowed_agents": ["palisade-shopper", "palisade-mcp"],
    "per_order_cap_cents": 100_000_000,
    "per_agent_daily_cap_cents": 100_000_000,
    "blocked_categories": [],
}

logger = structlog.stdlib.get_logger(__name__)


class Rules(BaseModel):
    allowed_agents: list[str]
    per_order_cap_cents: int
    per_agent_daily_cap_cents: int
    blocked_categories: list[str]

    def hash(self) -> str:
        """SHA-256 of the rules as canonical JSON: sorted keys, no spaces."""
        canonical = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class Policy(BaseModel):
    rules: Rules
    hash: str


class Purchase(BaseModel):
    """What an agent asks to buy, as the policy sees it."""

    agent_id: str
    total_cents: int
    categories: set[str]
    spent_cents_today: int


class PolicyRefused(Exception):
    """The merchant's policy refuses the purchase; the cart stays open."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(f"policy refused the purchase: {', '.join(reasons)}")
        self.reasons: list[str] = reasons


def evaluate(policy: Policy, purchase: Purchase) -> list[str]:
    """Every rule the purchase breaks, as reason codes; empty when it may go ahead."""
    rules = policy.rules
    reasons: list[str] = []
    if purchase.agent_id not in rules.allowed_agents:
        reasons.append("AGENT_NOT_ALLOWED")
    if purchase.total_cents > rules.per_order_cap_cents:
        reasons.append("ORDER_CAP_EXCEEDED")
    if (
        purchase.spent_cents_today + purchase.total_cents
        > rules.per_agent_daily_cap_cents
    ):
        reasons.append("AGENT_DAILY_CAP_EXCEEDED")
    reasons.extend(
        f"CATEGORY_BLOCKED:{category}"
        for category in sorted(purchase.categories & set(rules.blocked_categories))
    )
    return reasons


def spending(condition: ColumnElement[bool]) -> Select[int]:
    """Cents the orders matching `condition` hold or may hold.

    A `failed` order whose cart still waits on the repair job counts too: PayPal never answered
    its create, so the money may be held, and the job may still capture it.
    """
    return (
        select(func.coalesce(func.sum(OrderRecord.total_cents), 0))
        .join(CartRecord, CartRecord.id == OrderRecord.cart_id)
        .where(
            condition,
            or_(
                OrderRecord.status.in_(SPENDING_STATUSES),
                and_(
                    OrderRecord.status == "failed",
                    CartRecord.status == "checking_out",
                ),
            ),
        )
    )


def policy(record: PolicyRecord) -> Policy:
    rules = Rules.model_validate(record, from_attributes=True)
    return Policy(rules=rules, hash=record.hash)


@final
class Policies:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def ensure_current(self) -> None:
        """Write the merchant's rules as a new policy unless the one in force already is them.

        Orders and mandates keep the hash of the rules they passed, so an older policy stays
        readable, and a mandate signed under it is refused with POLICY_CHANGED.
        """
        rules = Rules.model_validate(MERCHANT_RULES)
        async with self._sessions.begin() as session:
            newest = await session.scalar(
                select(PolicyRecord.hash).order_by(PolicyRecord.id.desc()).limit(1)
            )
            if newest == rules.hash():
                return
            session.add(
                PolicyRecord(id=uuid7(), hash=rules.hash(), **rules.model_dump())
            )
            logger.info("policy_created", policy_hash=rules.hash())

    async def current(self, session: AsyncSession) -> Policy:
        """The policy in force, read inside the caller's transaction.

        Raises:
            LookupError: the merchant has no policy.
        """
        # UUIDv7 ids sort in creation order and never tie, which `created_at` does inside one transaction.
        record = await session.scalar(
            select(PolicyRecord).order_by(PolicyRecord.id.desc()).limit(1)
        )
        if record is None:
            raise LookupError("the merchant has no policy")
        return policy(record)

    @staticmethod
    async def spent_today(session: AsyncSession, agent_id: str) -> int:
        """Cents the agent's orders hold or may hold from the last day."""
        total = await session.scalar(
            spending(
                and_(
                    OrderRecord.agent_id == agent_id,
                    OrderRecord.created_at > func.now() - DAILY_WINDOW,
                )
            )
        )
        return int(total or 0)
