import secrets
from datetime import timedelta
from uuid import UUID

import pytest
from agent_fixtures import AGENT, seed
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.models import AgentNonceRecord
from palisade_api.agents.registry import Agents, BuiltinAgent
from palisade_api.maintenance import Housekeeping
from palisade_api.mandates.models import PasskeyChallengeRecord

pytestmark = pytest.mark.anyio


async def stored(sessions: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """How many nonces and challenges the database holds, whoever wrote them."""
    async with sessions() as session:
        nonces = await session.scalar(
            select(func.count()).select_from(AgentNonceRecord)
        )
        challenges = await session.scalar(
            select(func.count()).select_from(PasskeyChallengeRecord)
        )
    return {"nonces": nonces or 0, "challenges": challenges or 0}


async def test_nonces_and_challenges_older_than_ten_minutes_are_pruned(
    sessions: async_sessionmaker[AsyncSession], wallet_id: UUID
) -> None:
    await Agents(sessions).register(BuiltinAgent(AGENT, "Test Agent", seed()))
    spent, fresh = secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    async with sessions.begin() as session:
        for value, minutes in ((spent, 11), (fresh, 9)):
            written = func.now() - timedelta(minutes=minutes)
            await session.execute(
                insert(AgentNonceRecord).values(
                    agent_id=AGENT, nonce=value, seen_at=written
                )
            )
            await session.execute(
                insert(PasskeyChallengeRecord).values(
                    challenge=value, wallet_id=wallet_id, created_at=written
                )
            )
    before = await stored(sessions)

    removed = await Housekeeping(sessions).prune_rows()

    after = await stored(sessions)
    assert removed == {name: before[name] - after[name] for name in before}
    async with sessions() as session:
        nonces = set(
            await session.scalars(
                select(AgentNonceRecord.nonce).where(AgentNonceRecord.agent_id == AGENT)
            )
        )
        challenges = set(
            await session.scalars(
                select(PasskeyChallengeRecord.challenge).where(
                    PasskeyChallengeRecord.wallet_id == wallet_id
                )
            )
        )
    assert (nonces, challenges) == ({fresh}, {fresh})
