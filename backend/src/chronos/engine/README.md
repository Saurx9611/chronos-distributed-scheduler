# Queue engine boundary

Phase 2 implements explicit SQL transactions here, without FastAPI or handler imports.
`repository.py` owns DB-only transaction boundaries and pooling; `models.py` validates
submissions and represents ownership; `sql.py` contains the actual claim query.
See docs/database/TRANSACTIONS.md for safety arguments, lock ordering and limits.
No execution loop or recurring scheduler process is implemented yet.
