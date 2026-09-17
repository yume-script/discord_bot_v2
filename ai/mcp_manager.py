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

_client: MultiServerMCPClient | None = None
_tools: list = []


async def init_mcp() -> list:
    """
    앱 시작 시 1회만 호출. 이후엔 get_tools()로 캐시된 tool 목록을 재사용한다.
    MCP 서버 연결 실패(SSH 오류, 컨테이너 없음, 경로 오류 등)가 봇 전체를 죽이면 안 되므로
    여기서 예외를 흡수한다 - 실패해도 빈 tool 목록으로 계속 기동한다.
    """
    global _client, _tools

    if not settings.MCP_SERVERS_CONFIG_PATH.exists():
        _tools = []
        return _tools

    try:
        with settings.MCP_SERVERS_CONFIG_PATH.open(encoding="utf-8") as f:
            server_config = yaml.safe_load(f) or {}

        if not server_config:
            _tools = []
            return _tools

        _client = MultiServerMCPClient(server_config)
        _tools = await _client.get_tools()
    except Exception:
        log.exception(
            "MCP 서버 연결 실패 - mcp_servers.yaml 설정(SSH 키/경로/컨테이너명 등)을 확인할 것. "
            "MCP 없이 나머지 기능은 정상 기동한다."
        )
        _client = None
        _tools = []

    return _tools


def get_tools() -> list:
    return _tools
