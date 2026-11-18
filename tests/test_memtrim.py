"""쓰고 난 메모리 돌려주기: 리눅스면 malloc_trim, 아니어도 오류 없이"""
import asyncio


def test_release():
    from core import memtrim
    assert memtrim.release() in (True, False)


def test_loop_runs_and_cancels():
    from core import memtrim

    async def main():
        t = asyncio.create_task(memtrim.loop(every=0.01))
        await asyncio.sleep(0.05)
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    asyncio.run(main())
