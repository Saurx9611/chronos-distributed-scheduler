"""Bounded execution results, start timestamps and worker lifecycle state."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE job_attempts ADD COLUMN execution_started_at timestamptz,
        ADD COLUMN result jsonb,
        ADD CONSTRAINT attempts_result_bound CHECK (
            result IS NULL OR (jsonb_typeof(result)='object'
                AND octet_length(result::text)<=65536
                AND outcome IS NOT DISTINCT FROM 'succeeded'))
    """)
    op.execute("""
        ALTER TABLE worker_registrations ADD COLUMN state text NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','draining','stopped'))
    """)


def downgrade() -> None:
    op.drop_column("worker_registrations", "state")
    op.drop_column("job_attempts", "result")
    op.drop_column("job_attempts", "execution_started_at")
