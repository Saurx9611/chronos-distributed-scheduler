# ADR-0001: PostgreSQL as the coordination authority

Status: accepted for Phase 1. Date: 2026-10-27.

Context: Jobs, attempts and scheduling intent must survive worker crashes and be
inspectable without reconciling several infrastructure systems. The core engine
must be implemented from scratch.

Decision: Store authoritative state in PostgreSQL 18. Use READ COMMITTED, short
row-locking transactions, SKIP LOCKED for competing consumers, and explicit SQL
for critical paths. SQLAlchemy manages connections and noncritical persistence;
Alembic manages reviewed schema revisions. One write primary per deployment.

Alternatives: Redis plus PostgreSQL creates dual-write/reconciliation obligations.
Celery/Temporal/BullMQ replace the engine the project must demonstrate. Advisory
locks alone lack durable job ownership and do not encode attempt history.
SERIALIZABLE globally adds abort/retry costs without replacing ownership predicates.

Consequences: Operational simplicity and atomic job/attempt/event writes, but queue
writes compete with API reads, renewals increase table churn, and throughput is bounded
by database capacity. Measure vacuum, connection budget and claim plans before scaling.
No hard real-time scheduling, strict FIFO, global deadlock-free or exactly-once claim.

Validation: Phase 2 multi-session claim and rollback tests; Phase 3 crash recovery;
Phase 6 query plans and throughput measurements. Revisit on measured DB saturation.
