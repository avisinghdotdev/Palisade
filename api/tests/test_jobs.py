from typing import cast

from procrastinate.tasks import Task

from palisade_api.jobs import WORKER_QUEUES, jobs


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
