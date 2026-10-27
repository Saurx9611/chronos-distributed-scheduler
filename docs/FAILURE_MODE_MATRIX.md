# Phase 4 failure-mode matrix

The observations below come from real PostgreSQL and actual worker processes,
except where explicitly labeled a transaction/HTTP integration injection. Tests
must all pass before Phase 4 is marked complete. Final regression/CI references
are recorded in PROJECT_STATE.md and PHASE4_VERIFICATION.md.

| Trigger | Expected behavior | Executable test | Observed result |
| --- | --- | --- | --- |
| Worker SIGKILL after claim, before execution | Committed lease expires; no initial effect; fresh generation executes | test_fault_recovery.py::test_worker_killed_at_precise_pre_effect_boundary[after_claim] | Initial execution_started_at absent; effects 0 before kill; generation 1 lease_expired, generation 2 succeeded; one effect |
| Worker SIGKILL halfway through the two-phase handler | Prepared work is lost; uncommitted external effect not invented | Same test, [during_execute] | Durable preparation gate observed; initial requests/effects 0; replacement produced one effect |
| Worker killed after external commit, before ACK; idempotent destination | Retry may redeliver; stable business key deduplicates destination | test_kill_after_external_commit_demonstrates_duplicate_effect_risk[True] | Two delivery requests, one effect; first attempt lease_expired, replacement succeeded |
| Same boundary, deliberately non-idempotent destination | Repeated external work can duplicate effects | Same test, [False] | Two requests, **two effects**; passing test intentionally demonstrates this risk |
| Entire worker container paused beyond lease/heartbeat expiry | Monitoring marks lost; replacement can claim; old worker cannot finalize on resume | test_paused_old_worker_cannot_finalize_replacement | Lost registration observed; generation 2 succeeded; resumed work abandoned; separate stale success process rejected; one effect after two requests |
| Two live workers compete after expiration | Exactly one replacement ownership tuple is committed | test_two_real_workers_compete_after_expiration | Both competitors running before owner killed; two total attempts (generations 1/2), one replacement and one effect |
| Old owner submits success after replacement | Compare-and-update predicates reject it; rejection is auditable | Paused-worker test plus test_forged_owner_rejected / test_expiry_recovery_and_new_generation | Old generation success returned ownership_lost; new generation/result preserved; rejection audit records presented generation 1 |
| Handler repeatedly fails | Exponential jittered retry stops at max_attempts; dead-letter is terminal | test_repeated_real_handler_failures_reach_dead_letter | Three real failures, three attempts, three reason-coded events; dead_letter |
| Retry timing/budget boundaries | Full jitter uses min(cap,base*2^(attempt-1)); budget never resets inside one job | test_repeated_failure_uses_exact_jitter_policy_and_budget | Injected midpoint sampler observed bounds 2,4,5,5 and DB delays 1,2,2.5 seconds before retries; attempt 4 dead-lettered |
| Concurrent duplicate submission | Tenant/key uniqueness commits only one job | test_competing_consumers.py::test_simultaneous_idempotent_submissions | Eight synchronized submissions returned one identity; one job/key record |
| PostgreSQL unavailable during execution | Stop child on uncertain renewal; no guessed ACK; reconnect/recover | test_worker_processes.py::test_database_disconnect_abandons_then_recovers | Real DB stop/start; child absent during outage; generation 1 lease_expired, generation 2 succeeded |
| Runtime exceeds allowed deadline, even with future lease | Finalization rejected; reclamation honors execution deadline independently | test_timeout_recovery_and_heartbeat_loss_are_audited; real timeout process case | Dead-letter at exhausted budget; runtime_exceeded cause recorded under DB row lock |
| Heartbeat expires independently | Lost registration audited; no resurrection; do not steal a valid lease just from heartbeat state | test_timeout_recovery_and_heartbeat_loss_are_audited; test_worker_health_has_no_tokens_and_uses_database_expiry | lost recorded once; heartbeat rejected; health false from DB clock; no token exposure |
| Operator retry versus replay after already committed work | Retry keeps effect identity; replay intentionally requests a new effect | test_retry_and_replay_apis_drive_idempotent_real_effects | Authenticated retry kept one old effect after two requests; authenticated replay created one new effect under its new identity; both jobs succeeded |
| Two clients retry same operator command | Commit one new job and return it idempotently | test_simultaneous_commands_create_one_job | Four simultaneous commands returned one new identity |
| Command committed but response lost | Same key resolves prior result rather than create another job | test_committed_command_with_lost_response_is_not_created_twice | Real transaction committed before injected caller error; first HTTP response 503, next 201; one command record/new job |
| Unauthorized/foreign/revoked callers | No recovery mutation; scoped errors and rate limits | Authenticated HTTP contract and key/role/rate tests | 401/403/404/409/422/429 behavior checked against real DB; idempotent requests retain identity |
| History mutation attempt | Ordinary UPDATE/DELETE/TRUNCATE fail rather than silently rewrite history | test_audit_is_append_only | All three rejected; migrations still upgrade/downgrade/up successfully |
| SIGTERM drain or exhausted shutdown grace | Finish within budget or kill child before abandonment; never release a running effect | Existing sigterm / forced-drain process tests | Graceful completion exit 0; over-budget child abandoned and later reclaimed |

Sources: tests/process/test_fault_recovery.py, tests/process/test_worker_processes.py,
tests/integration/test_recovery_contracts.py and the pre-existing transactional suites.
Generated JSON facts/logs are under reports/worker-processes and uploaded by CI.
The intentionally unsafe test is not a recommended handler policy or a failed gate.

Delivery remains at-least-once. These tests establish the exercised ownership and
recovery invariants; they do not certify every network/failover/clock failure,
eliminate all deadlocks, or make remote effects part of the queue transaction.
