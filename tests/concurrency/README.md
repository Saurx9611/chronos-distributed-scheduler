# Transaction and process fault suites

Transaction/concurrency tests live in tests/integration, including
test_competing_consumers.py and test_transition_races.py. Phase 3's implemented
process suite lives in tests/process/test_worker_processes.py and exercises real
SIGTERM/SIGKILL, forced drain, concurrency and database stop/start with three workers.

Use real PostgreSQL sessions and barriers, not sleeps as synchronization.
Verify competing claims, rollback, ambiguous commit, expired renewal/completion,
recovery vs completion, cancellation vs recovery, and generation monotonicity.
Kill worker subprocesses before claim commit, after commit, during external effects,
and before completion commit. Exact wire-level COMMIT-response loss, primary failover
and every possible crash boundary are additional coverage tasks, not claimed as certified.
