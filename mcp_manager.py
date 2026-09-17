"""
mcp_servers.yaml을 읽어 MultiServerMCPClient로 연결하고,
봇 기동 시 1회만 연결/tool 캐싱한다 (app.py의 setup_hook에서 호출).

앞으로 MCP 서버가 여러 개 늘어날 걸 전제로, 서버 추가는 yaml만 고치면 되게 만든다.
"""
from __future__ import annotations

import yaml
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings

_client: MultiServerMCPClient | None = None
_tools: list = []


async def init_mcp() -> list:
    """앱 시작 시 1회만 호출. 이후엔 get_tools()로 캐시된 tool 목록을 재사용한다."""
    global _client, _tools

    if not settings.MCP_SERVERS_CONFIG_PATH.exists():
        _tools = []
        return _tools

    with settings.MCP_SERVERS_CONFIG_PATH.open(encoding="utf-8") as f:
        server_config = yaml.safe_load(f) or {}

    if not server_config:
        _tools = []
        return _tools

    _client = MultiServerMCPClient(server_config)
    _tools = await _client.get_tools()
    return _tools


def get_tools() -> list:
    return _tools
