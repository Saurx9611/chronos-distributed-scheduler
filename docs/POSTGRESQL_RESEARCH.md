# PostgreSQL research — reviewed 2026-10-27

PostgreSQL 18 is the stable design baseline. Links are versioned for reproducibility.
These are paraphrases and design inferences, not empirical performance findings.

| Official source | Documented behavior | Chronos decision/inference |
| --- | --- | --- |
| [Transactions](https://www.postgresql.org/docs/18/tutorial-transactions.html) | Transaction changes commit together or roll back together. | Persist claim, attempt and event together; execute effects after commit. |
| [Isolation](https://www.postgresql.org/docs/18/transaction-iso.html) | READ COMMITTED takes a new snapshot per command; concurrent updates can require predicate re-evaluation. Stronger isolation may abort transactions. | Use READ COMMITTED with explicit locks and conditional mutations; retry complete DB-only transactions on known retryable errors. |
| [Explicit locking](https://www.postgresql.org/docs/18/explicit-locking.html) | Row locks last until transaction end; deadlocks remain possible. | Never hold locks during execution; define lock order and bounded retry policy. |
| [SELECT locking clauses](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE) | SKIP LOCKED gives an inconsistent view useful to queue consumers; it does not eliminate table-lock waits. | Claim disjoint available rows without strict FIFO promises; use lock/statement timeouts. |
| [Partial indexes](https://www.postgresql.org/docs/18/indexes-partial.html) | Predicate implication determines whether the planner can use a partial index. | Use fixed state predicates matching claim/recovery SQL; never use now() in an index predicate. |
| [Multicolumn indexes](https://www.postgresql.org/docs/18/indexes-multicolumn.html) | Leading B-tree column constraints influence efficient access. | Start claim index with queue then available_at/id; validate plans against realistic distribution. |
| [LISTEN](https://www.postgresql.org/docs/18/sql-listen.html) | Registration takes effect at commit; startup has a race requiring a subsequent state inspection. | Commit LISTEN first, scan durable queue state, then process hints and continue polling. |
| [NOTIFY](https://www.postgresql.org/docs/18/sql-notify.html) | Notifications deliver on transaction boundaries, may fold duplicates, and have limited payloads. | Treat notification as optional latency optimization, never durable job transport. Keep listener transactions short and payloads free of secrets. |

Locking protects state transitions; it cannot prevent a paused handler from continuing
external effects after expiry. Durable tables, uniqueness constraints and generation
fencing supply the guarantees described in ARCHITECTURE.md. Polling remains the
correctness path; LISTEN/NOTIFY can be added after baseline measurements.

Other official implementation references reviewed: [uv lock/sync](https://docs.astral.sh/uv/concepts/projects/sync/),
[SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html),
[Pydantic settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/),
[FastAPI version guidance](https://fastapi.tiangolo.com/deployment/versions/),
[Next.js installation](https://nextjs.org/docs/app/getting-started/installation),
and [Tailwind Next.js installation](https://tailwindcss.com/docs/installation/framework-guides/nextjs).
