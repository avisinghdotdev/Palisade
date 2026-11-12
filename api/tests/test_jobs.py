import asyncio
from typing import cast

import pytest
from procrastinate.exceptions import ConnectorException
from procrastinate.tasks import Task
from procrastinate.testing import InMemoryConnector

import palisade_api.jobs as jobs_module
from palisade_api.jobs import WORKER_QUEUES, jobs, run_worker


def test_every_task_sits_on_a_queue_the_worker_listens_to() -> None:
    """A task on a queue missing from the worker's list would be queued and never run."""
    # The worker imports the task modules on start; here they are imported the same way.
    jobs.perform_import_paths()
    # Procrastinate types the registry's tasks with unknown signatures; only the queue matters.
    registered = cast("dict[str, Task[..., object, ...]]", jobs.tasks)
    ours = {
        task.queue
        for task in registered.values()
        if task.name.startswith("palisade_api.")
    }
    assert ours == set(WORKER_QUEUES)


@pytest.mark.anyio
async def test_a_worker_that_dies_starts_again_on_a_fresh_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A database outage ends the worker; the queue must get one again without a restart.

    On 2026-11-09 the worker's pool timed out and the hosted API ran two days with no worker.
    """
    pools: list[object] = []
    seen: list[object] = []
    working_again = asyncio.Event()

    def pool(**_settings: object) -> InMemoryConnector:
        connector = InMemoryConnector()
        pools.append(connector)
        return connector

    async def work(**_settings: object) -> None:
        seen.append(jobs.connector)
        if len(seen) == 1:
            raise ConnectorException
        working_again.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(jobs_module, "PsycopgConnector", pool)
    monkeypatch.setattr(jobs_module, "RESTART_PAUSE_SECONDS", 0)
    monkeypatch.setattr(jobs, "run_worker_async", work)

    async with run_worker("postgresql://unused", {}), asyncio.timeout(5):
        await working_again.wait()

    assert len(pools) == 2
    assert seen == pools
