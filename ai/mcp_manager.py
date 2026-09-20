"""
mcp_servers.yaml을 읽어 MultiServerMCPClient로 연결하고,
봇 기동 시 1회만 연결/tool 캐싱한다 (app.py의 setup_hook에서 호출).

앞으로 MCP 서버가 여러 개 늘어날 걸 전제로, 서버 추가는 yaml만 고치면 되게 만든다.
"""
from __future__ import annotations

import logging

import yaml
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings

log = logging.getLogger("mcp_manager")

_client: list[MultiServerMCPClient] | None = None
_tools: list = []


async def init_mcp() -> list:
    """
    앱 시작 시 1회만 호출. 이후엔 get_tools()로 캐시된 tool 목록을 재사용한다.
    MCP 서버 연결 실패(SSH 오류, 컨테이너 없음, 경로 오류 등)가 봇 전체를 죽이면 안 되므로
    여기서 예외를 흡수한다 - 실패해도 빈 tool 목록으로 계속 기동한다.

    [변경] 원래는 MultiServerMCPClient(전체 yaml)로 한 번에 합쳐서 불렀는데, 서버끼리
    도구 이름이 겹치면(예: bookoasis와 plex 둘 다 "get_library_stats") Gemini가
    "Duplicate function declaration" 오류로 대화 자체를 통째로 거부하는 문제가 있었다.
    이제 서버별로 따로 연결해서 모으고, 이름이 겹치면 나중에 나온 쪽을
    "{서버이름}_{도구이름}"으로 자동 개명해서 충돌을 피한다. 서버 하나가 실패해도
    그 서버만 건너뛰고 나머지는 정상 연결된다.
    """
    global _client, _tools

    if not settings.MCP_SERVERS_CONFIG_PATH.exists():
        _tools = []
        return _tools

    try:
        with settings.MCP_SERVERS_CONFIG_PATH.open(encoding="utf-8") as f:
            server_config = yaml.safe_load(f) or {}
    except Exception:
        log.exception("mcp_servers.yaml 파싱 실패 - MCP 없이 기동한다.")
        _client = None
        _tools = []
        return _tools

    if not server_config:
        _client = None
        _tools = []
        return _tools

    seen_names: dict[str, str] = {}  # 도구 이름 -> 그 이름을 먼저 쓴 서버명
    merged_tools: list = []
    clients: list[MultiServerMCPClient] = []

    for server_name, cfg in server_config.items():
        try:
            client = MultiServerMCPClient({server_name: cfg})
            server_tools = await client.get_tools()
        except Exception:
            log.exception(f"MCP 서버 '{server_name}' 연결 실패 - 이 서버만 건너뛴다.")
            continue

        clients.append(client)
        for tool in server_tools:
            if tool.name in seen_names:
                owner = seen_names[tool.name]
                new_name = f"{server_name}_{tool.name}"
                log.warning(
                    f"MCP 도구 이름 충돌: '{tool.name}' ('{owner}'와 '{server_name}' 둘 다 있음) "
                    f"- '{server_name}' 쪽을 '{new_name}'으로 개명함"
                )
                try:
                    tool.name = new_name
                except Exception:
                    try:
                        tool = tool.model_copy(update={"name": new_name})
                    except Exception:
                        log.warning(f"'{tool.name}' 개명 실패 - 이 도구는 건너뛴다.")
                        continue
            else:
                seen_names[tool.name] = server_name
            merged_tools.append(tool)

    _client = clients
    _tools = merged_tools
    return _tools


def get_tools() -> list:
    return _tools
