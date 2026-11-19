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
import time

import yaml
from mcp import types
from langchain_mcp_adapters.client import MultiServerMCPClient

from config import settings

log = logging.getLogger("mcp_manager")

# 서버 하나가 응답 없이 멈추면(ssh 대상 다운, docker exec 행 등) setup_hook 전체가 막혀서
# 봇이 로그인조차 못 한다 - 서버별로 이 시간 안에 도구 목록을 못 받으면 그 서버만 건너뛴다.
# SSH로 붙는 서버(bookoasis 등)는 부팅 직후 느릴 수 있어서 .env로 늘릴 수 있게 한다
MCP_CONNECT_TIMEOUT_SEC = int(os.environ.get("MCP_CONNECT_TIMEOUT_SEC", "30"))

_client: list[MultiServerMCPClient] | None = None
_tools: list = []
_admin_only_tools: set[str] = set()  # admin_only: true 서버에서 온 도구 이름 (개명 후 이름)
_tools_by_server: dict[str, list] = {}  # 서버 이름 -> 그 서버의 도구들 (poring_food/bookoasis.py가 사용)
_clients_by_server: dict[str, MultiServerMCPClient] = {}  # 서버 이름 -> 연결 설정이 들어 있는 클라이언트
# 부팅 때 도구 목록을 못 받은 서버도 설정은 남겨 둔다 - server_session()이 필요할 때 다시 붙는다
_configs_by_server: dict[str, dict] = {}

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
# LiteLLM MCP 게이트웨이: 한 연결로 여러 서버의 도구가 "{서버}-{도구}" 모양으로 온다 (버전에 따라 구분자가 다를 수 있어 여럿 본다)
_GATEWAY_SEPS = ("-", "__", "_", ".", "/")
_gateway_of: dict[str, tuple[str, str]] = {}   # 원래 서버 이름 -> (게이트웨이 서버 이름, 도구 이름 앞머리 "plex-")
# yaml에 적힌 게이트웨이 서버들 (부팅 때 연결에 실패해도 남겨 둔다 - server_session이 나중에 다시 붙는다)
_gateway_servers_cfg: dict[str, tuple[str, list[str]]] = {}   # 원래 서버 이름 -> (게이트웨이 서버 이름, 그 게이트웨이의 서버 목록)


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


def split_gateway_name(tool_name: str, servers) -> tuple[str, str, str] | None:
    """게이트웨이 도구 이름 'plex-get_library_stats' -> ('plex', 'get_library_stats', 'plex-'). 아는 서버가 아니면 None.
    서버 이름이 긴 것부터 본다 (docker_bookoasis와 docker_local처럼 앞이 같은 이름)"""
    for server in sorted(servers or [], key=len, reverse=True):
        for sep in _GATEWAY_SEPS:
            prefix = f"{server}{sep}"
            if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
                return server, tool_name[len(prefix):], prefix
    return None


def prepare_gateway_config(cfg: dict) -> tuple[dict, list[str], set[str]]:
    """게이트웨이(LiteLLM) 항목 -> (연결 설정, 거쳐 오는 서버 이름들, 그중 관리자 전용 서버들)"""
    cfg = dict(cfg or {})
    servers = [str(s) for s in cfg.pop("gateway_servers", None) or []]
    admin = {str(s) for s in cfg.pop("admin_only_servers", None) or []}
    cfg, _admin_all = prepare_server_config(cfg)
    # LITELLM_BASE_URL은 LLM 요청용이라 흔히 ".../v1"로 끝난다 - LiteLLM MCP는 /v1 없이 /mcp/ (/v1/mcp/는 307로 돌려보냄)
    if isinstance(cfg.get("url"), str):
        cfg["url"] = re.sub(r"/v1/+mcp/*$", "/mcp/", cfg["url"].rstrip())
    return cfg, servers, admin


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
    cfg.pop("gateway_servers", None)       # (게이트웨이 전용 키 - prepare_gateway_config가 읽는다)
    cfg.pop("admin_only_servers", None)
    env = cfg.get("env")
    if isinstance(env, dict):
        cfg["env"] = {k: _expand_env(v) for k, v in env.items()}
    # HTTP 연결(LiteLLM 게이트웨이 등)의 주소·헤더에도 ${VAR} (키를 yaml=git에 적지 않게)
    if isinstance(cfg.get("url"), str):
        cfg["url"] = _expand_env(cfg["url"])
    headers = cfg.get("headers")
    if isinstance(headers, dict):
        cfg["headers"] = {k: _expand_env(v) for k, v in headers.items()}
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
    global _client, _tools, _admin_only_tools, _tools_by_server, _clients_by_server, _configs_by_server

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
    tools_by_server: dict[str, list] = {}
    clients_by_server: dict[str, MultiServerMCPClient] = {}
    configs_by_server: dict[str, dict] = {}

    gateway_of: dict[str, tuple[str, str]] = {}
    _gateway_servers_cfg.clear()
    for server_name, raw_cfg in server_config.items():
        gateway_servers, gateway_admin = [], set()
        try:
            if isinstance(raw_cfg, dict) and raw_cfg.get("gateway_servers"):
                cfg, gateway_servers, gateway_admin = prepare_gateway_config(raw_cfg)
                admin_only = False
                for s in gateway_servers:
                    _gateway_servers_cfg[s] = (server_name, list(gateway_servers))
            else:
                cfg, admin_only = prepare_server_config(raw_cfg)
        except MissingEnvError as exc:
            log.error(f"MCP 서버 '{server_name}' 설정의 환경변수 {exc}가 .env에 없음 - 이 서버만 건너뛴다.")
            continue
        configs_by_server[server_name] = cfg
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
        clients_by_server[server_name] = client
        for tool in server_tools:
            owner_server, tool_admin = server_name, admin_only
            if gateway_servers:   # LiteLLM 게이트웨이: "plex-get_library_stats" -> plex 서버의 get_library_stats
                hit = split_gateway_name(tool.name, gateway_servers)
                if hit:
                    owner_server, base, prefix = hit
                    gateway_of.setdefault(owner_server, (server_name, prefix))
                    tool_admin = owner_server in gateway_admin
                    tool = _renamed(tool, base) or tool
                else:
                    tool_admin = bool(gateway_admin)   # 어느 서버 것인지 모르면 조심스럽게 (관리자 전용 서버가 있을 때)
            if tool.name in seen_names:
                owner = seen_names[tool.name]
                new_name = f"{owner_server}_{tool.name}"
                log.warning(
                    f"MCP 도구 이름 충돌: '{tool.name}' ('{owner}'와 '{owner_server}' 둘 다 있음) "
                    f"- '{owner_server}' 쪽을 '{new_name}'으로 개명함"
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
                seen_names[tool.name] = owner_server
            merged_tools.append(tool)
            tools_by_server.setdefault(owner_server, []).append(tool)
            if tool_admin:
                admin_only_tools.add(tool.name)

    _client = clients
    _tools = merged_tools
    _admin_only_tools = admin_only_tools
    _tools_by_server = tools_by_server
    _clients_by_server = clients_by_server
    _configs_by_server = configs_by_server
    _gateway_of.clear()
    _gateway_of.update(gateway_of)
    if gateway_of:
        log.info("LiteLLM MCP 게이트웨이로 붙은 서버: %s", sorted(gateway_of))
    if admin_only_tools:
        log.info("관리자 전용 MCP 도구: %s", sorted(admin_only_tools))
    return _tools


def _renamed(tool, new_name: str):
    """LangChain 도구의 이름만 바꾼 것 (호출은 서버의 원래 이름으로 그대로 간다). 못 바꾸면 None"""
    try:
        tool.name = new_name
        return tool
    except Exception:
        try:
            return tool.model_copy(update={"name": new_name})
        except Exception:
            log.warning(f"'{tool.name}' 개명 실패 - 원래 이름 그대로 쓴다.")
            return None


# 게이트웨이 도구 목록 기억 (앞머리 -> (받은 때, 도구들)). LiteLLM은 목록을 줄 때마다 등록된 서버를 모두 띄워 보느라
# 느리고 가끔 시간 초과로 일부가 빠지므로, 한 번 받은 그 서버의 도구 목록은 잠시 다시 쓴다.
GATEWAY_TOOLS_TTL_SEC = int(os.getenv("MCP_GATEWAY_TOOLS_TTL_SEC", "600"))
_gateway_tools_cache: dict[str, tuple[float, list]] = {}


class _PrefixedSession:
    """게이트웨이 세션을 원래 서버의 세션처럼: call_tool('get_library_stats') -> 게이트웨이의 'plex-get_library_stats'"""

    def __init__(self, session, prefix: str):
        self._s, self._p = session, prefix

    async def call_tool(self, name, arguments=None, *args, **kwargs):
        return await self._s.call_tool(f"{self._p}{name}", arguments, *args, **kwargs)

    async def list_tools(self, *args, **kwargs):
        hit = _gateway_tools_cache.get(self._p)
        if hit and time.monotonic() - hit[0] < GATEWAY_TOOLS_TTL_SEC and not args and not kwargs:
            return types.ListToolsResult(tools=list(hit[1]))
        res = await self._s.list_tools(*args, **kwargs)
        try:
            res.tools = [t.model_copy(update={"name": t.name[len(self._p):]}) for t in res.tools if t.name.startswith(self._p)]
            if res.tools:   # (빈 목록은 그 서버가 시간 초과로 빠진 것일 수 있어 기억하지 않는다)
                _gateway_tools_cache[self._p] = (time.monotonic(), list(res.tools))
        except Exception:  # noqa: BLE001 - 모양이 다르면 그대로
            pass
        return res

    def __getattr__(self, item):
        return getattr(self._s, item)


class _GatewaySessionCM:
    """prefix를 모르면(부팅 때 게이트웨이에 못 붙음) 세션을 연 뒤 도구 목록에서 그 서버의 앞머리를 찾는다 (없으면 '서버-')"""

    def __init__(self, cm, prefix: str, server: str = "", servers=None, gw_name: str = ""):
        self._cm, self._p, self._server, self._servers, self._gw_name = cm, prefix, server, servers or [], gw_name

    async def __aenter__(self):
        session = await self._cm.__aenter__()
        if not self._p:
            self._p = f"{self._server}-"
            try:
                for t in (await session.list_tools()).tools:
                    hit = split_gateway_name(t.name, self._servers)
                    if hit and hit[0] == self._server:
                        self._p = hit[2]
                        if self._gw_name:   # 다음부터는 바로 (앞머리를 다시 찾지 않게)
                            _gateway_of.setdefault(self._server, (self._gw_name, self._p))
                        break
            except Exception:  # noqa: BLE001 - 목록을 못 받으면 기본 앞머리로
                log.debug("게이트웨이 도구 목록을 못 받아 기본 앞머리 '%s'로", self._p, exc_info=True)
        return _PrefixedSession(session, self._p)

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)


def get_tools() -> list:
    return _tools


def get_server_tools(server_name: str) -> list:
    """특정 MCP 서버의 도구만 (이름 충돌로 "{서버}_{도구}"로 개명된 것 포함). 연결 실패한 서버면 빈 리스트."""
    return list(_tools_by_server.get(server_name, []))


def server_session(server_name: str):
    """
    특정 MCP 서버에 세션 하나를 열어서 여러 도구를 연달아 호출할 때 쓴다 (async with).

    get_tools()로 받은 LangChain 도구는 호출할 때마다 서버를 새로 띄운다(stdio면 매번 프로세스
    실행 - bookoasis는 매번 SSH + docker exec). 한 번에 여러 개를 부를 땐 이 세션을 쓰는 게
    훨씬 빠르다. 도구 이름은 서버가 정한 원래 이름(개명 전)이다.

    [변경] 부팅 때 연결이 실패/타임아웃된 서버(예: SSH가 늦게 붙은 bookoasis)는 예전엔 봇을 재시작할
    때까지 계속 "연결되어 있지 않음"이었다. 세션은 열 때마다 새로 붙으므로, 설정만 있으면 여기서 다시
    시도한다 (대화용 도구 목록에는 재시작 전까지 안 들어가지만 포링푸드 같은 세션 사용처는 살아난다).
    """
    if server_name in _gateway_of and server_name not in _clients_by_server:   # LiteLLM 게이트웨이로 옮긴 서버
        gw, prefix = _gateway_of[server_name]
        client = _clients_by_server.get(gw) or MultiServerMCPClient({gw: _configs_by_server[gw]})
        return _GatewaySessionCM(client.session(gw), prefix)
    if server_name in _gateway_servers_cfg and server_name not in _clients_by_server and _configs_by_server.get(_gateway_servers_cfg[server_name][0]):
        # 게이트웨이로 옮겼는데 부팅 때 게이트웨이에 못 붙음 - 지금 다시 붙고, 앞머리는 도구 목록에서 찾는다
        gw, servers = _gateway_servers_cfg[server_name]
        client = _clients_by_server.get(gw) or MultiServerMCPClient({gw: _configs_by_server[gw]})
        return _GatewaySessionCM(client.session(gw), "", server_name, servers, gw)
    client = _clients_by_server.get(server_name)
    if client is None:
        cfg = _configs_by_server.get(server_name)
        if cfg is None:
            raise RuntimeError(f"MCP 서버 '{server_name}'가 연결되어 있지 않음 (설정 없음 또는 .env 누락)")
        client = MultiServerMCPClient({server_name: cfg})
    return client.session(server_name)


async def fetch_server_tools(server_config: dict, server_name: str) -> dict:
    """mcp_servers.yaml 전체에서 서버 하나의 도구 {원래 이름: 도구} (scripts/new_content_notifier.py처럼 봇 밖에서 쓸 때).
    그 서버를 LiteLLM 게이트웨이로 옮겼으면 게이트웨이에 붙어 그 서버 것만 (앞머리 뗀 이름으로)"""
    if server_name in (server_config or {}):
        cfg, _ = prepare_server_config(server_config[server_name])
        tools = await MultiServerMCPClient({server_name: cfg}).get_tools()
        return {t.name: t for t in tools}
    for gw, raw in (server_config or {}).items():
        if isinstance(raw, dict) and server_name in (raw.get("gateway_servers") or []):
            cfg, servers, _admin = prepare_gateway_config(raw)
            out = {}
            for t in await MultiServerMCPClient({gw: cfg}).get_tools():
                hit = split_gateway_name(t.name, servers)
                if hit and hit[0] == server_name:
                    out[hit[1]] = _renamed(t, hit[1]) or t
            return out
    return {}


def is_admin_only_tool(tool_name: str) -> bool:
    """yaml에서 admin_only: true로 지정한 서버의 도구인지 (core/tool_policy.py가 사용)."""
    return tool_name in _admin_only_tools
