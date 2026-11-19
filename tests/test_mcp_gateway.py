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


def test_session_retries_gateway_after_failed_boot(tmp_path, monkeypatch):
    """부팅 때 게이트웨이에 못 붙어도 server_session('bookoasis')은 그때 다시 붙고 앞머리를 도구 목록에서 찾는다"""
    from ai import mcp_manager as mm
    from config import settings
    cfg_path = tmp_path / "mcp.yaml"
    cfg_path.write_text("""
litellm:
  transport: streamable_http
  url: "http://x/mcp/"
  gateway_servers: [plex, bookoasis]
""", encoding="utf-8")
    monkeypatch.setattr(settings, "MCP_SERVERS_CONFIG_PATH", cfg_path)
    calls = []
    boot = {"fail": True}

    class Sess(FakeSession):
        async def list_tools(self):
            return types.SimpleNamespace(tools=[types.SimpleNamespace(name="plex-get_recently_added"),
                                                types.SimpleNamespace(name="bookoasis__get_library_stats")])

    class CM(FakeCM):
        async def __aenter__(self):
            return Sess(self.log)

    class Client:
        def __init__(self, cfg):
            pass

        async def get_tools(self):
            if boot["fail"]:
                raise RuntimeError("연결 안 됨")
            return []

        def session(self, name):
            return CM(calls)
    monkeypatch.setattr(mm, "MultiServerMCPClient", Client)
    assert asyncio.run(mm.init_mcp()) == []

    async def use():
        async with mm.server_session("bookoasis") as s:
            await s.call_tool("get_library_stats", {})
    asyncio.run(use())
    assert calls == ["bookoasis__get_library_stats"]
    assert mm._gateway_of["bookoasis"] == ("litellm", "bookoasis__")


def test_gateway_url_drops_v1(monkeypatch):
    """LITELLM_BASE_URL이 .../v1이어도 MCP 주소는 /v1 없이 /mcp/ (LiteLLM은 /v1/mcp/를 307로 돌려보낸다)"""
    from ai.mcp_manager import prepare_gateway_config
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000/v1")
    cfg, servers, _ = prepare_gateway_config({"transport": "streamable_http", "url": "${LITELLM_BASE_URL}/mcp/", "gateway_servers": ["plex"]})
    assert cfg["url"] == "http://localhost:4000/mcp/" and servers == ["plex"]
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    assert prepare_gateway_config({"url": "${LITELLM_BASE_URL}/mcp/", "gateway_servers": ["plex"]})[0]["url"] == "http://localhost:4000/mcp/"


def test_prefixed_list_tools_cached():
    import asyncio
    from mcp import types
    from ai import mcp_manager as m

    calls = []

    class S:
        async def list_tools(self):
            calls.append(1)
            mk = lambda n: types.Tool(name=n, inputSchema={"type": "object"})  # noqa: E731
            return types.ListToolsResult(tools=[mk("bookoasis-search_books"), mk("plex-x")])

    m._gateway_tools_cache.clear()
    s = m._PrefixedSession(S(), "bookoasis-")
    a = asyncio.run(s.list_tools())
    b = asyncio.run(s.list_tools())
    assert [t.name for t in a.tools] == ["search_books"] == [t.name for t in b.tools]
    assert len(calls) == 1
    m._gateway_tools_cache.clear()
