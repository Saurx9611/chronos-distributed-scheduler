# ADR-0006: Supervised independent workers and isolated handlers

Status: implemented in Phase 3, 2026-10-27.

The worker is a standalone Python service, not FastAPI background tasks. A parent
asyncio loop claims only free execution slots and runs registration/recovery/control
operations separately from lease/finalization operations. Bounded thread pools and
DB pools keep blocking psycopg operations out of the event loop.

Each handler executes in a fresh fixed Python child process. Alternatives were
in-process async handlers (cooperative cancellation cannot stop blocking code),
threads (cannot forcibly stop work), and a reusable child pool (harder to associate
and terminate one uncertain lease without disturbing other attempts). Per-attempt
processes cost startup time and memory but offer simple, bounded failure isolation.
Measure overhead before optimizing; no performance claim follows from this decision.

Handler names map to a static registry and an operator-configured subset. Payloads
are bounded typed data; no dynamic module/function/command path is accepted. The
parent uses a constant Python executable/module with shell=False and strips DB
credentials from the child environment. This is trusted-code isolation, not a
sandbox for arbitrary submitted programs. Docker runs nonroot, drops capabilities,
uses a read-only filesystem and bounds memory/process count.

Renewal returns DB-measured remaining lease/deadline time. Use request-start
monotonic time minus a safety margin to bound child lifetime locally. Database
predicates are still authoritative. Uncertain renewal stops the child and abandons
the attempt; it never authorizes releasing a lease while effects might continue.
Ambiguous DB outcomes are reconciled by expiry/recovery, not blind effect replay.

SIGTERM drains with renewal up to the configured budget, then kills/reaps children.
SIGKILL requires expiry recovery; Linux children also arm parent-death SIGKILL.
The existing generation/token checks reject stale finalization. Persisting bounded
results in migration 0002 demonstrates actual workloads without logging their data.

Consequences: independent scaling and responsive heartbeats, at the cost of more
processes/connections and startup overhead. External exactly-once effects still
require destination idempotency/fencing. The simulated notification workload has
no real network effects. Production auth, least-privilege DB roles, recurring
generation and performance testing remain separate gates.

Validation: real PostgreSQL runtime tests plus a dedicated Linux Compose CI job
running three workers, SIGTERM/SIGKILL, forced drain, timeout/retry/allowlist and
database stop/restart scenarios. Artifacts contain redacted lifecycle JSON and facts.
