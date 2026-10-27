# Phase 2/3 acceptance suite

Use real PostgreSQL sessions and barriers, not sleeps as synchronization.
Verify competing claims, rollback, ambiguous commit, expired renewal/completion,
recovery vs completion, cancellation vs recovery, and generation monotonicity.
Kill worker subprocesses before claim commit, after commit, during external effects,
and before completion commit. No such tests are claimed to pass in Phase 1.
