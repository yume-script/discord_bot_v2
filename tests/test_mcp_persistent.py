"""자주 부르는 MCP 서버는 세션을 열어 두고 다시 쓴다 (ai/mcp_manager.py) - 가짜 클라이언트로"""
import asyncio
from contextlib import asynccontextmanager

import pytest

mm = pytest.importorskip("ai.mcp_manager")


class FakeSession:
    def __init__(self, n):
        self.n, self.fail = n, None

    async def call_tool(self, name, args=None):
        if self.fail:
            raise self.fail
        return f"{name}@{self.n}"


class FakeClient:
    opened = []

    def __init__(self, cfg):
        pass

    @asynccontextmanager
    async def session(self, name):
        s = FakeSession(len(FakeClient.opened) + 1)
        FakeClient.opened.append(s)
        yield s


@pytest.fixture
def env(monkeypatch):
    FakeClient.opened = []
    monkeypatch.setattr(mm, "MultiServerMCPClient", FakeClient)
    monkeypatch.setattr(mm, "_configs_by_server", {"bookoasis": {"transport": "stdio"}, "other": {"transport": "stdio"}})
    monkeypatch.setattr(mm, "_clients_by_server", {})
    monkeypatch.setattr(mm, "_gateway_of", {})
    monkeypatch.setattr(mm, "_gateway_servers_cfg", {})
    monkeypatch.setattr(mm, "PERSISTENT", {"bookoasis"})
    monkeypatch.setattr(mm, "_persist", {})


def test_reuses_and_reopens_after_broken_call(env):
    async def main():
        mm._main_loop = asyncio.get_running_loop()
        for _ in range(3):
            async with mm.server_session("bookoasis") as s:
                assert await s.call_tool("get_active_users") == "get_active_users@1"
        assert len(FakeClient.opened) == 1                       # 세 번 불러도 한 번만 띄움
        FakeClient.opened[0].fail = ConnectionError("broken pipe")
        async with mm.server_session("bookoasis") as s:
            with pytest.raises(ConnectionError):
                await s.call_tool("x")
        async with mm.server_session("bookoasis") as s:        # 끊긴 것은 다시 연다
            assert await s.call_tool("y") == "y@2"
        from mcp.shared.exceptions import McpError
        from mcp.types import ErrorData
        FakeClient.opened[1].fail = McpError(ErrorData(code=-1, message="tool error"))
        async with mm.server_session("bookoasis") as s:
            with pytest.raises(McpError):
                await s.call_tool("z")
        FakeClient.opened[1].fail = None
        async with mm.server_session("bookoasis") as s:        # 도구 오류는 세션을 닫지 않음
            assert await s.call_tool("w") == "w@2"
        async with mm.server_session("other") as s:            # 목록에 없는 서버는 예전처럼 그때그때
            assert await s.call_tool("q") == "q@3"
        async with mm.server_session("other") as s:
            assert await s.call_tool("q") == "q@4"
        await mm.close_persistent()
        assert mm._persist == {}
    asyncio.run(main())
    mm._main_loop = None


def test_other_loop_does_not_use_kept_session(env):
    async def main():
        async with mm.server_session("bookoasis") as s:
            return await s.call_tool("a")
    mm._main_loop = object()                                  # 봇 루프가 아닌 곳 (봇 밖 스크립트 등)
    try:
        assert asyncio.run(main()) == "a@1" and mm._persist == {}
    finally:
        mm._main_loop = None
