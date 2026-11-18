"""쓰고 난 메모리를 운영체제에 돌려주기 (스왑 줄이기).

파이썬은 큰 작업(그림 만들기, LLM 응답·도구 결과, 큰 채널 읽기 …)이 끝나 객체를 버려도 그 메모리를 프로세스가
계속 쥐고 있어서, 오래 켜 두면 쓰지 않는 메모리가 스왑으로 밀려난다. 가비지 수집 뒤 glibc malloc_trim(0)으로
빈 메모리를 돌려준다. 리눅스(glibc)가 아니면 가비지 수집만 한다.
"""
from __future__ import annotations

import asyncio
import ctypes
import ctypes.util
import gc
import logging

log = logging.getLogger("memtrim")
_fn = None


def _trim_fn():
    global _fn
    if _fn is None:
        try:
            lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
            _fn = lib.malloc_trim if hasattr(lib, "malloc_trim") else False
        except OSError:
            _fn = False
    return _fn


def release() -> bool:
    """가비지 수집 + 빈 메모리 돌려주기. 돌려줬으면 True"""
    gc.collect()
    fn = _trim_fn()
    if not fn:
        return False
    try:
        return bool(fn(0))
    except Exception:  # noqa: BLE001
        return False


async def loop(every: float = 600) -> None:
    """켜 있는 동안 every초(기본 10분)마다"""
    while True:
        await asyncio.sleep(every)
        try:
            await asyncio.to_thread(release)
        except Exception:  # noqa: BLE001
            log.debug("메모리 돌려주기 실패", exc_info=True)
