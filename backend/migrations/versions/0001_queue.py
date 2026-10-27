"""Initial transactional queue schema."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    ddl = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    # No values are interpolated; every statement is versioned static DDL.
    for statement in ddl.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table in (
        "audit_events",
        "job_events",
        "submission_keys",
        "schedule_occurrences",
        "job_attempts",
        "jobs",
        "worker_registrations",
        "schedules",
        "tenants",
    ):
        op.drop_table(table)
