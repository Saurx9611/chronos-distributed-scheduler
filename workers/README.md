# Independent worker processes

The installed entry point is `python -m chronos.worker` in backend/src/chronos/worker.
It uses the queue repository independently of FastAPI, with bounded child-process
execution, an explicit handler allowlist, lease renewal, registration heartbeats,
timeouts, recovery and SIGTERM draining. Compose's workers profile starts three
independent workers after one migration job.

See docs/WORKER_RUNTIME.md for configuration, demo commands, failure behavior and
test reproduction. Real signal/outage scenarios are in tests/process.
