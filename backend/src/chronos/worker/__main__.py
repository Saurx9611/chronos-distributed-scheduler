import asyncio
import logging
import signal
import sys

from chronos.worker.logging import emit
from chronos.worker.runtime import WorkerRuntime
from chronos.worker.settings import WorkerSettings


async def main() -> None:
    # Pool reset failures can otherwise print raw driver exception tracebacks.
    # The supervisor reports database failures through its redacted JSON events.
    pool_logger = logging.getLogger("sqlalchemy.pool")
    pool_logger.addHandler(logging.NullHandler())
    pool_logger.propagate = False
    settings = WorkerSettings()
    runtime = WorkerRuntime(settings)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, runtime.request_stop)
        except NotImplementedError:  # Windows native asyncio; Compose uses Linux signals.
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(runtime.request_stop))
    await runtime.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        emit("worker_fatal", error_class=type(exc).__name__)
        sys.exit(1)
