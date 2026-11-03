"""The merchant's policy as the console reads it, and the filing mode it carries."""

from collections.abc import Iterator

import httpx2
import pytest
from agent_fixtures import allow
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.main import app
from palisade_api.policy.engine import Policies, Policy
from palisade_api.policy.router import policies as policies_dependency

pytestmark = pytest.mark.anyio


@pytest.fixture
def console(sessions: async_sessionmaker[AsyncSession]) -> Iterator[None]:
    app.dependency_overrides[policies_dependency] = lambda: Policies(sessions)
    yield
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("console")
async def test_the_filing_mode_changes_in_place_and_outlives_a_new_policy(
    sessions: async_sessionmaker[AsyncSession], client: httpx2.AsyncClient
) -> None:
    policies = Policies(sessions)
    older = await allow(sessions, per_order_cap_cents=50_000)
    before = Policy.model_validate_json((await client.get("/api/policy")).content)
    assert (before.hash, before.dispute_filing) == (older, "review")

    changed = await client.put("/api/policy/dispute-filing", json={"mode": "automatic"})

    after = Policy.model_validate_json(changed.content)
    assert (after.hash, after.dispute_filing) == (older, "automatic")
    # The merchant's rules change: a new policy, the same setting.
    await policies.ensure_current()
    current = await policies.get()
    assert current.hash != older
    assert current.dispute_filing == "automatic"
    assert (
        await client.put("/api/policy/dispute-filing", json={"mode": "sometimes"})
    ).status_code == 422
