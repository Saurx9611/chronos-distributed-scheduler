# Worker process boundary

Phase 3 adds a Python entry point using the installed `chronos` engine package.
Each process has bounded concurrent executions, a registered handler allowlist,
independent lease renewal, and SIGTERM draining. No worker daemon exists yet.
Never use the API server's background tasks as the execution runtime.
