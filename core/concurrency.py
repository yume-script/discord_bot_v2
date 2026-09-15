"""
기존 봇의 이미지 생성 로직(horde_gen.py)은 30분 재시도 방식이었고,
동시 요청이 몰리면 막히는 문제가 있었다 (카톡/디스코드 발신자 구분이 안 됐던 게 원인 중 하나).

여기서는 UserRef.key를 기준으로 한 asyncio.Queue 워커풀을 공용으로 제공해서,
이미지 생성뿐 아니라 앞으로 비슷한 "무거운 작업 + 동시성 제한"이 필요한 기능이
전부 이 한 곳을 재사용하게 한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from core.user_ref import UserRef


@dataclass
class Job:
    user: UserRef
    coro_fn: Callable[[], Awaitable[None]]
    future: asyncio.Future = field(default_factory=asyncio.Future)


class WorkerPool:
    """이름 있는 작업 종류(예: 'image_gen')별로 독립된 큐 + 워커 수를 갖는다."""

    def __init__(self, name: str, max_concurrency: int = 2):
        self.name = name
        self.max_concurrency = max_concurrency
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []

    def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker_loop(i)) for i in range(self.max_concurrency)
        ]

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                result = await job.coro_fn()
                if not job.future.done():
                    job.future.set_result(result)
            except Exception as exc:  # noqa: BLE001 - 워커는 죽으면 안 됨
                if not job.future.done():
                    job.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def submit(self, user: UserRef, coro_fn: Callable[[], Awaitable[None]]):
        """작업을 큐에 넣고, 결과(or 예외)를 기다릴 수 있는 future를 반환한다."""
        job = Job(user=user, coro_fn=coro_fn)
        await self._queue.put(job)
        return await job.future

    def queue_size(self) -> int:
        return self._queue.qsize()


# 기능별로 풀을 나눠서 쓴다. 예: image_gen 풀이 막혀도 다른 기능엔 영향 없음.
image_gen_pool = WorkerPool("image_gen", max_concurrency=2)
