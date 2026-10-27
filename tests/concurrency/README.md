# Process fault suite acceptance (Phase 3)

Phase 2's implemented transaction/concurrency tests live in tests/integration,
including test_competing_consumers.py and test_transition_races.py. This directory
reserves the later worker process-crash/fault-injection suite.

Use real PostgreSQL sessions and barriers, not sleeps as synchronization.
Verify competing claims, rollback, ambiguous commit, expired renewal/completion,
recovery vs completion, cancellation vs recovery, and generation monotonicity.
Kill worker subprocesses before claim commit, after commit, during external effects,
and before completion commit. Those process-crash cases are not implemented in Phase 2.
