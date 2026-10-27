# ADR-0004: Polling correctness, optional notification hints

Status: accepted for Phase 1. Date: 2026-10-27.

Decision: Poll durable job state with bounded idle backoff and jitter. Add LISTEN/NOTIFY
only if measured idle latency/load warrants it. Never deliver job data through NOTIFY.
LISTEN uses a dedicated session connection, commits registration, and then scans queue
state; reconnect repeats this sequence. Notifications trigger scans, not execution.

Alternatives: Notification-only dispatch loses wakeups during disconnect and startup.
An external message broker adds a second coordination system without demonstrated need.

Consequences: Correctness survives lost/folded notifications and connection restarts.
Polling adds queries; dedicated listeners consume connections and bypass transaction
poolers. Measure before enabling. Future tests must discard notifications and still
observe eventual eligible job claims.
