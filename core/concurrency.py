"""
기존 봇의 이미지 생성 로직(horde_gen.py)은 30분 재시도 방식이었고,
동시 요청이 몰리면 막히는 문제가 있었다 (카톡/디스코드 발신자 구분이 안 됐던 게 원인 중 하나).

여기서는 asyncio.Queue 워커풀을 공용으로 제공해서, 이미지 생성뿐 아니라 앞으로 비슷한
"무거운 작업 + 동시성 제한"이 필요한 기능이 전부 이 한 곳을 재사용하게 한다.
UserRef.key(카톡/디스코드 구분된 전역 유일 키)별로 동시에 걸어둘 수 있는 작업 수를 제한해서,
한 사람이 연달아 요청해 워커를 전부(작업당 최대 30분) 점유하는 일을 막는다.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from config import settings
from core.user_ref import UserRef

log = logging.getLogger("concurrency")


class QueueLimitError(RuntimeError):
    """한 유저가 이미 최대 개수만큼 작업을 걸어둔 상태에서 또 제출했을 때."""


@dataclass
class Job:
    user: UserRef
    coro_fn: Callable[[], Awaitable[None]]
    future: asyncio.Future = field(default_factory=asyncio.Future)


class WorkerPool:
    """이름 있는 작업 종류(예: 'image_gen')별로 독립된 큐 + 워커 수를 갖는다."""

    def __init__(self, name: str, max_concurrency: int = 2, max_pending_per_user: int = 2):
        self.name = name
        self.max_concurrency = max_concurrency
        self.max_pending_per_user = max_pending_per_user
        self._queue: asyncio.Queue[Job] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._pending_by_user: dict[str, int] = {}  # UserRef.key -> 대기+실행 중인 작업 수

    def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker_loop(i)) for i in range(self.max_concurrency)
        ]
        log.info("%s 워커풀 시작: %d개", self.name, len(self._workers))

    def _healthy_worker_count(self) -> int:
        return sum(1 for w in self._workers if not w.done())

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
        # 워커가 없으면(start() 미호출, 혹은 워커가 전부 죽음) 큐에 넣어도 아무도 꺼내지 않아
        # 호출부가 영원히 await 상태로 멈춘다 - "아무 반응도 없고 로그도 없음"의 원인이 되므로
        # 조용히 멈추지 말고 즉시 에러를 올린다.
        if self._healthy_worker_count() == 0:
            log.error(
                "%s 워커풀에 살아있는 워커가 없습니다 (start() 호출 누락 또는 워커 비정상 종료). "
                "작업을 큐에 넣지 않고 즉시 실패시킵니다.",
                self.name,
            )
            self.start()  # 자동 복구 시도
            if self._healthy_worker_count() == 0:
                raise RuntimeError(f"{self.name} 워커풀을 시작할 수 없습니다.")

        pending = self._pending_by_user.get(user.key, 0)
        if pending >= self.max_pending_per_user:
            raise QueueLimitError(
                f"이미 진행 중인 요청이 {pending}개 있어요. 끝난 뒤에 다시 요청해주세요."
            )

        job = Job(user=user, coro_fn=coro_fn)
        self._pending_by_user[user.key] = pending + 1
        try:
            await self._queue.put(job)
            log.info("%s 큐에 작업 제출 (user=%s, 대기=%d)", self.name, user.key, self._queue.qsize())
            return await job.future
        finally:
            # 정상 완료/예외/호출부 취소 어느 경우든 카운트를 되돌린다.
            remaining = self._pending_by_user.get(user.key, 1) - 1
            if remaining > 0:
                self._pending_by_user[user.key] = remaining
            else:
                self._pending_by_user.pop(user.key, None)

    def queue_size(self) -> int:
        return self._queue.qsize()


# 기능별로 풀을 나눠서 쓴다. 예: image_gen 풀이 막혀도 다른 기능엔 영향 없음.
image_gen_pool = WorkerPool("image_gen", max_concurrency=settings.IMAGE_GEN_MAX_CONCURRENCY)
