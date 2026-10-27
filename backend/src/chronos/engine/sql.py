"""Critical parameterized SQL, shared verbatim by runtime and documentation."""

CLAIM_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT id FROM jobs
    WHERE queue = :queue
      AND state IN ('queued', 'retry_wait')
      AND available_at <= statement_timestamp()
      AND attempts < max_attempts
    ORDER BY priority DESC, available_at, id
    LIMIT :limit FOR UPDATE SKIP LOCKED
)
UPDATE jobs AS j
SET state = 'running', attempts = j.attempts + 1, generation = j.generation + 1,
    lease_token = gen_random_uuid(), lease_owner = :worker_id,
    lease_expires_at = LEAST(
        clock_timestamp() + make_interval(secs => :lease_seconds),
        clock_timestamp() + make_interval(secs => j.execution_timeout_s)),
    execution_deadline = clock_timestamp() + make_interval(secs => j.execution_timeout_s),
    started_at = COALESCE(j.started_at, clock_timestamp()),
    attempt_started_at = clock_timestamp(), updated_at = clock_timestamp()
FROM candidates AS c WHERE j.id = c.id
RETURNING j.*
"""

# Executed only AFTER SELECT ... FOR UPDATE has acquired the job row lock.
OWNER_PREDICATE = """
id = :job_id AND state = 'running' AND lease_owner = :worker_id
AND lease_token = :token AND generation = :generation
AND lease_expires_at > clock_timestamp() AND execution_deadline > clock_timestamp()
"""
