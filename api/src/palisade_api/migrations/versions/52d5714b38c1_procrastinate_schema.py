"""Procrastinate schema.

Revision ID: 52d5714b38c1
Revises: 27ee30737974
Create Date: 2026-10-21 07:23:00.256686
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "52d5714b38c1"
down_revision: str | Sequence[str] | None = "27ee30737974"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = Path(__file__).with_suffix(".sql")

# Dropped in this order: the tables (their triggers go with them), then the functions, then the
# types the function signatures use. One function returns the jobs row type, so it goes first.
FETCH_JOB = "procrastinate_fetch_job_v2(character varying[], bigint)"
TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
FUNCTIONS = (
    "procrastinate_defer_jobs_v1(procrastinate_job_to_defer_v1[])",
    "procrastinate_defer_periodic_job_v2(character varying, character varying, character varying, character varying, integer, character varying, bigint, jsonb)",
    "procrastinate_finish_job_v1(bigint, procrastinate_job_status, boolean)",
    "procrastinate_cancel_job_v1(bigint, boolean, boolean)",
    "procrastinate_retry_job_v1(bigint, timestamp with time zone, integer, character varying, character varying)",
    "procrastinate_retry_job_v2(bigint, timestamp with time zone, integer, character varying, character varying)",
    "procrastinate_notify_queue_job_inserted_v1()",
    "procrastinate_notify_queue_abort_job_v1()",
    "procrastinate_trigger_function_status_events_insert_v1()",
    "procrastinate_trigger_function_status_events_update_v1()",
    "procrastinate_trigger_function_scheduled_events_v1()",
    "procrastinate_trigger_abort_requested_events_procedure_v1()",
    "procrastinate_unlink_periodic_defers_v1()",
    "procrastinate_register_worker_v1()",
    "procrastinate_unregister_worker_v1(bigint)",
    "procrastinate_update_heartbeat_v1(bigint)",
    "procrastinate_prune_stalled_workers_v1(float)",
)
TYPES = (
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
)


def upgrade() -> None:
    # Straight to the driver, because text() would read the `:` in the PL/pgSQL as parameters.
    # psycopg still gets an empty parameter set and so treats `%` as a placeholder marker,
    # which doubling escapes, as Procrastinate's own schema loader does.
    schema = SCHEMA.read_text(encoding="utf-8").replace("%", "%%")
    op.get_bind().exec_driver_sql(schema)


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {FETCH_JOB}")
    for table in TABLES:
        op.drop_table(table)
    for function in FUNCTIONS:
        op.execute(f"DROP FUNCTION {function}")
    for type_name in TYPES:
        op.execute(f"DROP TYPE {type_name}")
