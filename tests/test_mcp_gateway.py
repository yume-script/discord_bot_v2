"""LiteLLM MCP 게이트웨이: 앞머리 뗀 도구 이름, 관리자 전용, 서버 세션, ${VAR} 치환"""
import asyncio
import types

import pytest

pytest.importorskip("langchain_mcp_adapters")


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeSession:
    def __init__(self, log):
        self.log = log

    async def call_tool(self, name, arguments=None):
        self.log.append(name)
        return {"name": name}

    async def list_tools(self):
        return types.SimpleNamespace(tools=[])


class FakeCM:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return FakeSession(self.log)

    async def __aexit__(self, *exc):
        return False


def make_client(calls, cfgs):
    class FakeClient:
        def __init__(self, cfg):
            cfgs.append(cfg)
            self.name = next(iter(cfg))

        async def get_tools(self):
            if self.name == "litellm":
                return [FakeTool(n) for n in ("plex-get_library_stats", "bookoasis-get_library_stats",
                                              "docker_bookoasis-list_containers", "youtube-search", "mystery")]
            return [FakeTool("read_file")]

        def session(self, name):
            return FakeCM(calls)
    return FakeClient


def test_split_gateway_name():
    from ai.mcp_manager import split_gateway_name
    servers = ["docker_bookoasis", "bookoasis", "plex"]
    assert split_gateway_name("plex-get_library_stats", servers) == ("plex", "get_library_stats", "plex-")
    assert split_gateway_name("docker_bookoasis-list", servers) == ("docker_bookoasis", "list", "docker_bookoasis-")
    assert split_gateway_name("bookoasis__get", servers) == ("bookoasis", "get", "bookoasis__")
    assert split_gateway_name("other-x", servers) is None


def test_gateway_tools_admin_and_session(tmp_path, monkeypatch):
    from ai import mcp_manager as mm
    from config import settings
    cfg_path = tmp_path / "mcp.yaml"
    cfg_path.write_text("""
filesystem:
  admin_only: true
  transport: stdio
  command: npx
litellm:
  transport: streamable_http
  url: "${LITELLM_BASE_URL}/mcp/"
  headers:
    x-litellm-api-key: "Bearer ${LITELLM_MCP_KEY}"
  gateway_servers: [youtube, plex, bookoasis, docker_bookoasis]
  admin_only_servers: [docker_bookoasis]
""", encoding="utf-8")
    monkeypatch.setattr(settings, "MCP_SERVERS_CONFIG_PATH", cfg_path)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    monkeypatch.setenv("LITELLM_MCP_KEY", "sk-test")
    calls, cfgs = [], []
    monkeypatch.setattr(mm, "MultiServerMCPClient", make_client(calls, cfgs))
    tools = asyncio.run(mm.init_mcp())
    names = sorted(t.name for t in tools)
    # 앞머리를 떼고, 겹치는 이름(get_library_stats)은 나중 서버 것을 "{서버}_{도구}"로 (예전과 같게)
    assert names == ["bookoasis_get_library_stats", "get_library_stats", "list_containers", "mystery", "read_file", "search"]
    gw_cfg = next(c["litellm"] for c in cfgs if "litellm" in c)
    assert gw_cfg["url"] == "http://127.0.0.1:4000/mcp/" and gw_cfg["headers"]["x-litellm-api-key"] == "Bearer sk-test"
    assert "gateway_servers" not in gw_cfg and "admin_only_servers" not in gw_cfg
    assert mm.is_admin_only_tool("list_containers") and mm.is_admin_only_tool("read_file")
    assert mm.is_admin_only_tool("mystery")            # 어느 서버 것인지 모름 + 관리자 전용 서버가 있음 -> 조심스럽게
    assert not mm.is_admin_only_tool("search") and not mm.is_admin_only_tool("get_library_stats")
    assert [t.name for t in mm.get_server_tools("plex")] == ["get_library_stats"]
    assert [t.name for t in mm.get_server_tools("bookoasis")] == ["bookoasis_get_library_stats"]

    async def use():
        async with mm.server_session("bookoasis") as s:
            await s.call_tool("get_library_stats", {"db_type": "webtoon"})
    asyncio.run(use())
    assert calls == ["bookoasis-get_library_stats"]   # 게이트웨이에는 앞머리 붙인 이름으로


def test_fetch_server_tools_through_gateway(monkeypatch):
    from ai import mcp_manager as mm
    monkeypatch.setenv("LITELLM_BASE_URL", "http://x")
    monkeypatch.setenv("LITELLM_MCP_KEY", "k")
    monkeypatch.setattr(mm, "MultiServerMCPClient", make_client([], []))
    cfg = {"litellm": {"transport": "streamable_http", "url": "${LITELLM_BASE_URL}/mcp/", "headers": {"x": "${LITELLM_MCP_KEY}"},
                       "gateway_servers": ["plex", "bookoasis"]}}
    assert sorted(asyncio.run(mm.fetch_server_tools(cfg, "plex"))) == ["get_library_stats"]
    assert asyncio.run(mm.fetch_server_tools(cfg, "nothing")) == {}
