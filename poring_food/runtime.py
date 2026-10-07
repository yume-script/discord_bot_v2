"""
포링푸드 파이프라인과 봇 사이의 연결 지점.

포링푸드 코드는 원래 cron용 동기 코드(requests, 15초 타임아웃 LLM 호출 여러 번)라서, 봇
이벤트 루프를 막지 않도록 cogs/poring_food.py가 별도 스레드(asyncio.to_thread)에서 돌린다.
그 스레드 안에서 봇 기능(디스코드 채널에 글 올리기, 봇이 연결해 둔 MCP 도구 호출)이 필요할
때는 이 모듈로 봇 이벤트 루프에 작업을 넘기고 결과를 기다린다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

_loop: asyncio.AbstractEventLoop | None = None
_discord_sender: Callable[[str], Awaitable[None]] | None = None


def bind(loop: asyncio.AbstractEventLoop, discord_sender: Callable[[str], Awaitable[None]] | None) -> None:
    """cogs/poring_food.py가 봇이 뜰 때 한 번 호출한다."""
    global _loop, _discord_sender
    _loop = loop
    _discord_sender = discord_sender


def run_on_bot_loop(coro: Awaitable[Any], timeout: float) -> Any:
    """[스레드에서 호출] 봇 이벤트 루프에서 코루틴을 실행하고 결과를 기다린다."""
    if _loop is None:
        raise RuntimeError("포링푸드 런타임이 봇에 연결되지 않았습니다 (cogs/poring_food.py 미로드).")
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    try:
        return future.result(timeout=timeout)
    except BaseException:
        future.cancel()
        raise


def has_discord_sender() -> bool:
    return _discord_sender is not None


def send_discord(text: str, timeout: float = 30) -> None:
    """[스레드에서 호출] 봇 계정으로 포링푸드 방송 채널에 글을 올린다."""
    if _discord_sender is None:
        raise RuntimeError("디스코드 전송 함수가 연결되지 않았습니다.")
    run_on_bot_loop(_discord_sender(text), timeout=timeout)
