"""Recovery reasons, retry policy, command idempotency, API credentials and effect identity."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE jobs ADD COLUMN effect_key uuid;
        UPDATE jobs SET effect_key=id;
        ALTER TABLE jobs ALTER COLUMN effect_key SET NOT NULL,
            ALTER COLUMN effect_key SET DEFAULT gen_random_uuid(),
            ADD COLUMN retry_base_seconds double precision NOT NULL DEFAULT 1
                CHECK (retry_base_seconds>0 AND retry_base_seconds<=60),
            ADD COLUMN retry_cap_seconds double precision NOT NULL DEFAULT 300
                CHECK (retry_cap_seconds>=retry_base_seconds AND retry_cap_seconds<=3600);
        ALTER TABLE job_attempts ADD COLUMN recovery_reason text;
        ALTER TABLE job_events ADD COLUMN from_state text, ADD COLUMN to_state text,
            ADD COLUMN reason text NOT NULL DEFAULT 'legacy_event';
        ALTER TABLE audit_events ADD COLUMN reason text NOT NULL DEFAULT 'legacy_event',
            ADD COLUMN presented_generation bigint CHECK (presented_generation>=0);
        ALTER TABLE worker_registrations DROP CONSTRAINT worker_registrations_state_check;
        ALTER TABLE worker_registrations ADD CONSTRAINT worker_registrations_state_check
            CHECK (state IN ('active','draining','stopped','lost'));
        CREATE INDEX jobs_runtime_recovery_idx ON jobs(execution_deadline,id)
            WHERE state IN ('running','cancel_requested');
        CREATE INDEX jobs_effect_key_idx ON jobs(effect_key)
    """)
    op.execute("""
        CREATE TABLE job_command_keys (
            tenant_id uuid NOT NULL REFERENCES tenants(id),
            key text NOT NULL CHECK (octet_length(key) BETWEEN 1 AND 128),
            action text NOT NULL CHECK (action IN ('retry','replay')),
            source_job_id uuid NOT NULL,
            new_job_id uuid NOT NULL,
            request_hash text NOT NULL CHECK (length(request_hash)=64),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant_id,key),
            FOREIGN KEY (tenant_id,source_job_id) REFERENCES jobs(tenant_id,id),
            FOREIGN KEY (tenant_id,new_job_id) REFERENCES jobs(tenant_id,id)
                DEFERRABLE INITIALLY DEFERRED
        );
        CREATE TABLE api_keys (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id),
            token_hash text NOT NULL UNIQUE CHECK (length(token_hash)=64),
            role text NOT NULL CHECK (role IN ('viewer','operator','admin')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz
        );
        CREATE TABLE api_rate_windows (
            api_key_id uuid NOT NULL REFERENCES api_keys(id),
            window_start timestamptz NOT NULL,
            requests integer NOT NULL CHECK (requests>0),
            PRIMARY KEY(api_key_id,window_start)
        )
    """)
    # History is append-only for ordinary SQL, not tamper-proof against a DB owner.
    op.execute("""
        CREATE FUNCTION chronos_preserve_history() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Chronos history is append-only' USING ERRCODE='42501'; END; $$;
        CREATE TRIGGER preserve_job_events BEFORE UPDATE OR DELETE ON job_events
            FOR EACH ROW EXECUTE FUNCTION chronos_preserve_history();
        CREATE TRIGGER preserve_audit_events BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION chronos_preserve_history();
        CREATE TRIGGER preserve_job_events_truncate BEFORE TRUNCATE ON job_events
            FOR EACH STATEMENT EXECUTE FUNCTION chronos_preserve_history();
        CREATE TRIGGER preserve_audit_events_truncate BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION chronos_preserve_history()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER preserve_job_events ON job_events")
    op.execute("DROP TRIGGER preserve_audit_events ON audit_events")
    op.execute("DROP TRIGGER preserve_job_events_truncate ON job_events")
    op.execute("DROP TRIGGER preserve_audit_events_truncate ON audit_events")
    op.execute("DROP FUNCTION chronos_preserve_history()")
    for table in ("api_rate_windows", "api_keys", "job_command_keys"):
        op.drop_table(table)
    op.drop_index("jobs_runtime_recovery_idx", table_name="jobs")
    op.drop_index("jobs_effect_key_idx", table_name="jobs")
    op.execute("UPDATE worker_registrations SET state='stopped' WHERE state='lost'")
    op.drop_constraint("worker_registrations_state_check", "worker_registrations")
    op.create_check_constraint(
        "worker_registrations_state_check",
        "worker_registrations",
        "state IN ('active','draining','stopped')",
    )
    for column in ("effect_key", "retry_base_seconds", "retry_cap_seconds"):
        op.drop_column("jobs", column)
    op.drop_column("job_attempts", "recovery_reason")
    for column in ("from_state", "to_state", "reason"):
        op.drop_column("job_events", column)
    op.drop_column("audit_events", "reason")
    op.drop_column("audit_events", "presented_generation")
