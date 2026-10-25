"""The agents the merchant knows, and the two Palisade runs itself."""

from dataclasses import dataclass
from typing import final

import structlog
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.keys import encode_public_key, private_key
from palisade_api.agents.models import AgentRecord

logger = structlog.stdlib.get_logger(__name__)


@dataclass(frozen=True)
class BuiltinAgent:
    """An agent Palisade operates; its private key is a setting, its public key the registry's."""

    id: str
    name: str
    seed: str


class Agent(BaseModel):
    id: str
    name: str


class UnknownAgent(LookupError):
    def __init__(self, agent_id: str) -> None:
        super().__init__(f"no agent has the id {agent_id}")


@final
class Agents:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def register(self, agent: BuiltinAgent) -> None:
        """Put the agent's name and public key in the registry; a changed seed replaces the key."""
        key = encode_public_key(private_key(agent.seed).public_key())
        async with self._sessions.begin() as session:
            record = await session.get(AgentRecord, agent.id)
            if record is None:
                session.add(AgentRecord(id=agent.id, name=agent.name, public_key=key))
                logger.info("agent_registered", agent_id=agent.id)
                return
            if record.public_key != key:
                record.public_key = key
                logger.warning("agent_key_rotated", agent_id=agent.id)
            if record.name != agent.name:
                record.name = agent.name
                logger.info("agent_renamed", agent_id=agent.id, name=agent.name)

    async def public_keys(self) -> dict[str, str]:
        """Every agent's public key by agent id, as a signature verifier resolves them."""
        async with self._sessions() as session:
            records = (await session.scalars(select(AgentRecord))).all()
        return {record.id: record.public_key for record in records}

    async def get(self, agent_id: str) -> Agent:
        async with self._sessions() as session:
            record = await session.get(AgentRecord, agent_id)
        if record is None:
            raise UnknownAgent(agent_id)
        return Agent(id=record.id, name=record.name)
