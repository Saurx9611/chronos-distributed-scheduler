"""Per-claim child supervision and conservative lease guards."""

import asyncio
import contextlib
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from sqlalchemy.exc import SQLAlchemyError

from chronos.engine.models import Claim, OwnershipLost
from chronos.engine.repository import QueueRepository
from chronos.worker.demo_http import gate
from chronos.worker.logging import emit
from chronos.worker.runner import Reply, run_handler
from chronos.worker.settings import WorkerSettings


async def db_call[T](
    executor: ThreadPoolExecutor,
    timeout: float,
    function: Callable[..., T],
    *args: object,
    **kwargs: object,
) -> T:
    # Cancelling this await cannot cancel a running DB call. Its outcome is ambiguous;
    # callers abandon execution rather than start/repeat external effects.
    future = asyncio.get_running_loop().run_in_executor(
        executor, partial(function, *args, **kwargs)
    )
    return await asyncio.wait_for(future, timeout=max(0.001, timeout))


class Execution:
    def __init__(
        self,
        repository: QueueRepository,
        settings: WorkerSettings,
        executor: ThreadPoolExecutor,
        claim: Claim,
    ) -> None:
        self.repo, self.settings, self.executor, self.claim = repository, settings, executor, claim
        self.valid_until = 0.0
        self.deadline = 0.0
        self.fields = {
            "job_id": str(claim.job_id),
            "worker_id": str(claim.worker_id),
            "generation": claim.generation,
            "job_type": claim.job_type,
        }

    async def renew(self) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        timeout = self.settings.db_call_timeout_s
        if self.valid_until:
            timeout = min(timeout, self.valid_until - started)
            if timeout <= 0:
                raise OwnershipLost()
        window = await db_call(
            self.executor,
            timeout,
            self.repo.renew,
            self.claim,
            lease_seconds=self.settings.lease_seconds,
        )
        # Start at request dispatch, not response receipt, so network/commit delay
        # cannot extend the local safety estimate. DB predicates remain authoritative.
        self.valid_until = started + window.lease_remaining_s - 0.25
        self.deadline = started + window.execution_remaining_s - 0.25
        if loop.time() >= min(self.valid_until, self.deadline):
            raise OwnershipLost()
        emit("lease_renewed", **self.fields)

    async def keep_lease(self) -> None:
        while True:
            remaining = self.valid_until - asyncio.get_running_loop().time()
            await asyncio.sleep(max(0, min(self.settings.renew_interval_s, remaining)))
            await self.renew()

    async def record(self, reply: Reply) -> None:
        if reply.status == "ok":
            await db_call(
                self.executor,
                self.settings.db_call_timeout_s,
                self.repo.acknowledge,
                self.claim,
                result=reply.result,
            )
            emit("job_succeeded", **self.fields)
        else:
            await db_call(
                self.executor,
                self.settings.db_call_timeout_s,
                self.repo.fail,
                self.claim,
                retryable=reply.status == "retry",
                error_code=reply.error_code,
            )
            emit(
                "job_failed",
                **self.fields,
                retryable=reply.status == "retry",
                error_code=reply.error_code,
            )

    async def run(self) -> None:
        child: asyncio.Task[Reply] | None = None
        keeper: asyncio.Task[None] | None = None
        try:
            # Revalidate ownership after the claim commit, before any handler effect.
            await self.renew()
            if self.claim.job_type not in self.settings.allowlist:
                await self.record(Reply(status="permanent", error_code="handler_not_allowed"))
                return
            await db_call(
                self.executor,
                min(
                    self.settings.db_call_timeout_s,
                    self.valid_until - asyncio.get_running_loop().time(),
                ),
                self.repo.execution_started,
                self.claim,
            )
            emit("job_started", **self.fields)
            remaining = min(self.deadline, self.valid_until) - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise OwnershipLost()
            child = asyncio.create_task(run_handler(self.claim))
            keeper = asyncio.create_task(self.keep_lease())
            # The handler has a fixed attempt deadline; renewal cannot extend it.
            done, _ = await asyncio.wait(
                {child, keeper},
                timeout=max(0, self.deadline - asyncio.get_running_loop().time()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if keeper in done:
                keeper.result()  # Propagates uncertain/lost ownership, fail closed.
                raise OwnershipLost()
            if child not in done:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)
                reply = Reply(status="retry", error_code="execution_timeout")
            else:
                reply = child.result()
            if reply.status == "ok":
                await gate("before_ack", self.claim.job_id, self.claim.generation)
            keeper.cancel()
            await asyncio.gather(keeper, return_exceptions=True)
            await self.record(reply)
        except asyncio.CancelledError:
            emit("job_abandoned", **self.fields, reason="worker_shutdown")
            raise
        except (OwnershipLost, SQLAlchemyError, TimeoutError) as exc:
            if child is not None:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)
            if isinstance(exc, OwnershipLost):
                with contextlib.suppress(SQLAlchemyError, TimeoutError):
                    await db_call(
                        self.executor,
                        self.settings.db_call_timeout_s,
                        self.repo.record_ownership_rejection,
                        self.claim,
                    )
            # A cancellation request also invalidates renewal. Stop work FIRST,
            # then acknowledge cancellation if this is still the authorized owner.
            with contextlib.suppress(OwnershipLost, SQLAlchemyError, TimeoutError):
                await db_call(
                    self.executor,
                    self.settings.db_call_timeout_s,
                    self.repo.acknowledge_cancellation,
                    self.claim,
                )
            emit("job_abandoned", **self.fields, reason=type(exc).__name__)
        except Exception:
            if child is not None:
                child.cancel()
                await asyncio.gather(child, return_exceptions=True)
            try:
                await self.record(Reply(status="retry", error_code="worker_execution_error"))
            except OwnershipLost, SQLAlchemyError, TimeoutError:
                emit("job_abandoned", **self.fields, reason="finalization_uncertain")
        finally:
            tasks = [task for task in (child, keeper) if task is not None]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
