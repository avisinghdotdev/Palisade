"""Housekeeping: rows that only matter for a while are removed once they no longer can."""

from typing import final

import structlog
from procrastinate import JobContext
from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from palisade_api.agents.models import AgentNonceRecord
from palisade_api.agents.signatures import MAX_AGE
from palisade_api.jobs import MAINTENANCE_QUEUE, context_value, jobs
from palisade_api.mandates.models import PasskeyChallengeRecord
from palisade_api.mandates.passkeys import CHALLENGE_LIFETIME

# A nonce guards against replay only while its signature could still pass the age check.
NONCE_KEPT = MAX_AGE * 2
CHALLENGE_KEPT = CHALLENGE_LIFETIME * 2
# Finished jobs stay readable for a week; failed ones a month, for whoever looks into them.
SUCCEEDED_JOBS_KEPT_HOURS = 7 * 24
FAILED_JOBS_KEPT_HOURS = 30 * 24

logger = structlog.stdlib.get_logger(__name__)


@final
class Housekeeping:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def prune_rows(self) -> dict[str, int]:
        """Delete spent nonces and unused passkey challenges; how many of each went."""
        async with self._sessions.begin() as session:
            nonces = await session.scalars(
                delete(AgentNonceRecord)
                .where(AgentNonceRecord.seen_at < func.now() - NONCE_KEPT)
                .returning(AgentNonceRecord.nonce)
            )
            challenges = await session.scalars(
                delete(PasskeyChallengeRecord)
                .where(PasskeyChallengeRecord.created_at < func.now() - CHALLENGE_KEPT)
                .returning(PasskeyChallengeRecord.challenge)
            )
            return {
                "nonces": len(nonces.all()),
                "challenges": len(challenges.all()),
            }


@jobs.periodic(cron="17 * * * *")
@jobs.task(queue=MAINTENANCE_QUEUE, lock="prune", pass_context=True)
async def prune(context: JobContext, timestamp: int) -> None:
    removed = await context_value(context, "housekeeping", Housekeeping).prune_rows()
    manager = context.app.job_manager
    await manager.delete_old_jobs(nb_hours=SUCCEEDED_JOBS_KEPT_HOURS)
    await manager.delete_old_jobs(
        nb_hours=FAILED_JOBS_KEPT_HOURS,
        include_failed=True,
        include_cancelled=True,
        include_aborted=True,
    )
    logger.info("rows_pruned", scheduled_for=timestamp, **removed)
