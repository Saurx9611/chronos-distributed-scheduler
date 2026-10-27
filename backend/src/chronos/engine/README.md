# Queue engine boundary

Phase 2 implements explicit SQL transactions here. The engine must not import FastAPI
or worker handlers. See ARCHITECTURE.md and docs/database/README.md for invariants.
No claims, renewals, completions, or recovery are implemented in Phase 1.
