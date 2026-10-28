from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.activity.models import ActivityRecord
from palisade_api.activity.store import Activities

pytestmark = pytest.mark.anyio


async def test_details_keep_a_bounded_part_of_every_text_and_list(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    marker = uuid4().hex

    await Activities(sessions).record(
        "signature_refused",
        marker=marker,
        long="r" * 301,
        exact="e" * 300,
        tags=[str(number) for number in range(25)],
        nested={"calls": [{"query": "q" * 400, "count": 3, "ok": True}]},
    )

    async with sessions() as session:
        details = await session.scalar(
            select(ActivityRecord.details).where(
                ActivityRecord.details.contains({"marker": marker})
            )
        )
    assert details == {
        "marker": marker,
        "long": f"{'r' * 300}…",
        "exact": "e" * 300,
        "tags": [str(number) for number in range(20)],
        "nested": {"calls": [{"query": f"{'q' * 300}…", "count": 3, "ok": True}]},
    }
