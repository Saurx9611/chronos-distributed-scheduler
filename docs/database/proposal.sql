-- Phase 1 executable proposal. NOT an application migration; CI validates in isolation.
CREATE TABLE tenants (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200)
);

CREATE TABLE schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    enabled boolean NOT NULL DEFAULT true,
    queue text NOT NULL CHECK (length(queue) BETWEEN 1 AND 100),
    job_type text NOT NULL CHECK (length(job_type) BETWEEN 1 AND 100),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'
        AND octet_length(payload::text) <= 65536),
    interval_seconds integer NOT NULL CHECK (interval_seconds >= 60),
    next_fire_at timestamptz NOT NULL,
    misfire_policy text NOT NULL DEFAULT 'skip'
        CHECK (misfire_policy IN ('skip', 'catch_up')),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    UNIQUE (tenant_id, id)
);

CREATE TABLE jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    queue text NOT NULL CHECK (length(queue) BETWEEN 1 AND 100),
    job_type text NOT NULL CHECK (length(job_type) BETWEEN 1 AND 100),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'
        AND octet_length(payload::text) <= 65536),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN
        ('queued', 'running', 'retry_wait', 'cancel_requested', 'succeeded', 'dead_letter', 'cancelled')),
    available_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 10),
    generation bigint NOT NULL DEFAULT 0,
    lease_token uuid,
    lease_owner uuid,
    lease_expires_at timestamptz,
    execution_timeout_s integer NOT NULL DEFAULT 900 CHECK (execution_timeout_s BETWEEN 1 AND 900),
    replay_of uuid,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    finished_at timestamptz,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, replay_of) REFERENCES jobs(tenant_id, id),
    CHECK (attempts BETWEEN 0 AND max_attempts AND generation >= attempts),
    CHECK (
        (state IN ('running', 'cancel_requested') AND lease_token IS NOT NULL
            AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state NOT IN ('running', 'cancel_requested') AND lease_token IS NULL
            AND lease_owner IS NULL AND lease_expires_at IS NULL)
    ),
    CHECK ((state IN ('succeeded', 'dead_letter', 'cancelled')) = (finished_at IS NOT NULL))
);

CREATE TABLE attempts (
    job_id uuid NOT NULL REFERENCES jobs(id),
    generation bigint NOT NULL CHECK (generation > 0),
    lease_token uuid NOT NULL UNIQUE,
    owner uuid NOT NULL,
    started_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    finished_at timestamptz,
    outcome text CHECK (outcome IN ('succeeded', 'retryable_error', 'permanent_error',
        'lease_expired', 'cancelled')),
    error_code text CHECK (length(error_code) <= 100),
    PRIMARY KEY (job_id, generation),
    CHECK ((outcome IS NULL) = (finished_at IS NULL))
);

CREATE TABLE schedule_occurrences (
    schedule_id uuid NOT NULL,
    tenant_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    scheduled_for timestamptz NOT NULL,
    job_id uuid NOT NULL UNIQUE,
    PRIMARY KEY (schedule_id, revision, scheduled_for),
    FOREIGN KEY (tenant_id, schedule_id) REFERENCES schedules(tenant_id, id),
    FOREIGN KEY (tenant_id, job_id) REFERENCES jobs(tenant_id, id)
);

CREATE TABLE submission_keys (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    key text NOT NULL CHECK (octet_length(key) BETWEEN 1 AND 128),
    request_hash text NOT NULL CHECK (length(request_hash) = 64),
    job_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp(),
    PRIMARY KEY (tenant_id, key),
    FOREIGN KEY (tenant_id, job_id) REFERENCES jobs(tenant_id, id)
);

CREATE TABLE job_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id),
    generation bigint NOT NULL CHECK (generation >= 0),
    event_type text NOT NULL,
    actor_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);

CREATE INDEX jobs_claim_idx ON jobs (queue, available_at, id)
    WHERE state IN ('queued', 'retry_wait');
CREATE INDEX jobs_recovery_idx ON jobs (lease_expires_at, id)
    WHERE state IN ('running', 'cancel_requested');
CREATE INDEX jobs_tenant_history_idx ON jobs (tenant_id, created_at DESC, id DESC);
CREATE INDEX schedules_due_idx ON schedules (next_fire_at, id) WHERE enabled;
CREATE INDEX events_job_idx ON job_events (job_id, id);
