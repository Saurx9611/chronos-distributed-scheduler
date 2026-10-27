# ADR-0003: Dedicated, replicable recurring scheduler

Status: accepted for Phase 1. Date: 2026-10-27.

Context: Running recurring generation inside API workers creates accidental duplicate
loops and couples availability to HTTP scaling. Leader election adds lifecycle complexity.

Decision: A separate process, with optional multiple replicas, locks due schedule rows
using SKIP LOCKED. In a single transaction insert the job, insert its unique occurrence
identity `(schedule_id,revision,scheduled_for)`, and advance the cursor. Insert conflict
must either refer to an existing occurrence/job or roll back any new candidate job;
never commit an orphan job after ON CONFLICT DO NOTHING. Prefer checking occurrence
identity while holding the schedule lock, with uniqueness as the final safeguard.

Only UTC fixed intervals initially. Skip misfire policy emits the latest due slot and
advances past DB now; catch_up emits at most 100 due slots per transaction. Record
skipped count as a schedule event/metric in Phase 4. Disabled schedules do not generate.
Edits lock the schedule, increment revision and set a future cursor; existing jobs remain.
Enabling a schedule chooses a future cursor, preventing implicit historical replay.
Retain occurrence identities across history retention. Hard schedule deletion deferred.

Alternatives: Global singleton/advisory-lock leader is simpler initially but requires
leader recovery and does not replace deduplication. Worker-side generation couples
execution capacity to scheduling. External cron systems split scheduling authority.

Consequences: Independent deployment and bounded catch-up, extra process and database
polling. No cron, timezone or DST support initially. Two revisions can intentionally
create distinct jobs for a time slot only via explicit future scheduling edits.

Validation: Two scheduler replicas, crash between each write, ambiguous COMMIT,
long outage, disable/edit races and occurrence uniqueness in Phase 4.
