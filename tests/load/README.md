# Benchmark protocol (Phase 6)

Record commit, CPU/RAM/OS, PostgreSQL settings/version, connection budgets, queue
depth, payload distribution, handler runtime, lease durations, poll interval,
worker count/concurrency, warmup, duration, and at least three repetitions.
Measure jobs/s, queue wait and claim latency p50/p95/p99, execution latency,
lock waits, retries, failures, reclaim lag, CPU/RAM, and DB connections.
Compare 1/2/4/8 workers with equal total connection budgets where possible.
No performance measurements or throughput promises exist yet.
