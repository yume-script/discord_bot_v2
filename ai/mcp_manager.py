"""
mcp_servers.yaml을 읽어 MultiServerMCPClient로 연결하고,
봇 기동 시 1회만 연결/tool 캐싱한다 (app.py의 setup_hook에서 호출).

앞으로 MCP 서버가 여러 개 늘어날 걸 전제로, 서버 추가는 yaml만 고치면 되게 만든다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import yaml
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings

log = logging.getLogger("mcp_manager")

# 서버 하나가 응답 없이 멈추면(ssh 대상 다운, docker exec 행 등) setup_hook 전체가 막혀서
# 봇이 로그인조차 못 한다 - 서버별로 이 시간 안에 도구 목록을 못 받으면 그 서버만 건너뛴다.
MCP_CONNECT_TIMEOUT_SEC = 30

_client: list[MultiServerMCPClient] | None = None
_tools: list = []
_admin_only_tools: set[str] = set()  # admin_only: true 서버에서 온 도구 이름 (개명 후 이름)

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingEnvError(KeyError):
    """yaml의 ${VAR} 자리표시자에 대응하는 환경변수(.env)가 없을 때."""


def _expand_env(value):
    if not isinstance(value, str):
        return value

    def _sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise MissingEnvError(name)
        return os.environ[name]

    return _ENV_PLACEHOLDER.sub(_sub, value)


def prepare_server_config(cfg: dict) -> tuple[dict, bool]:
    """
    mcp_servers.yaml의 서버 설정 하나를 MultiServerMCPClient에 넘길 형태로 정리한다.
    scripts/new_content_notifier.py도 같은 yaml을 읽으므로 이 함수를 같이 쓴다.

    - env 값의 "${VAR}" 자리표시자를 환경변수(.env, config/settings.py가 로드)로 치환한다.
      API 키/토큰을 yaml(=git 추적 파일)에 직접 적지 않기 위한 장치다. 변수가 없으면
      MissingEnvError - 빈 값으로 띄우면 인증 실패 원인을 찾기 어렵다.
    - 이 봇 전용 키 "admin_only"(true면 그 서버의 도구 전부를 관리자만 실행 가능)를 떼어내서
      두 번째 반환값으로 돌려준다 (MCP 클라이언트는 모르는 키라 그대로 넘기면 안 됨).
    """
    cfg = dict(cfg or {})
    admin_only = bool(cfg.pop("admin_only", False))
    env = cfg.get("env")
    if isinstance(env, dict):
        cfg["env"] = {k: _expand_env(v) for k, v in env.items()}
    return cfg, admin_only


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
    global _client, _tools, _admin_only_tools

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
    admin_only_tools: set[str] = set()

    for server_name, raw_cfg in server_config.items():
        try:
            cfg, admin_only = prepare_server_config(raw_cfg)
        except MissingEnvError as exc:
            log.error(f"MCP 서버 '{server_name}' 설정의 환경변수 {exc}가 .env에 없음 - 이 서버만 건너뛴다.")
            continue
        try:
            client = MultiServerMCPClient({server_name: cfg})
            server_tools = await asyncio.wait_for(client.get_tools(), timeout=MCP_CONNECT_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            log.error(f"MCP 서버 '{server_name}' 연결 타임아웃({MCP_CONNECT_TIMEOUT_SEC}s) - 이 서버만 건너뛴다.")
            continue
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
            if admin_only:
                admin_only_tools.add(tool.name)

    _client = clients
    _tools = merged_tools
    _admin_only_tools = admin_only_tools
    if admin_only_tools:
        log.info("관리자 전용 MCP 도구: %s", sorted(admin_only_tools))
    return _tools


def get_tools() -> list:
    return _tools


def is_admin_only_tool(tool_name: str) -> bool:
    """yaml에서 admin_only: true로 지정한 서버의 도구인지 (core/tool_policy.py가 사용)."""
    return tool_name in _admin_only_tools
