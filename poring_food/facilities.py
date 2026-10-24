"""
감시탑과 초소 - 실제 docker 컨테이너가 멈추거나 다시 켜지면 오아시스 마을의 "감시탑 불 꺼짐/결계 균열" 사건이 된다.

숫자(실적)는 없고 "켜져 있나"만 본다. 매시 회차 시작 때 docker MCP(list_containers, all=true)로 한 번 조회한다.
- 서버 두 대 = 성벽의 두 구역: etc(docker_local) = 수비대 본부 쪽 성벽, plex01(docker_bookoasis) = 도서관·극장 쪽 성벽
- 컨테이너 = 성벽의 감시탑/초소/마을 시설 (FACILITIES). 표에 없는 컨테이너는 무시한다.
- 사건 (LLM 호출 없음, agents.inject_event로 그 범위 사람들 받은편지함에):
  · 멈춤(꺼짐/재시작 반복/unhealthy/사라짐) → "OO 불 꺼짐" - 다시 켜질 때까지 연대기 "진행 중인 일"
  · 다시 켜짐 → "OO 다시 불 켜짐" (진행 중인 일 해결)
  · 켜진 채로 시작 시각만 바뀜(재시작) → 그 범위 사람들에게만 "잠깐 꺼졌다 돌아옴"
  · 한 서버의 표에 있는 컨테이너가 전부 새로 시작됨 → 그 구역 전체 "결계 균열" 사건 하나로 묶는다
- 처음 조회(기준 없음)는 기록만 하고 사건을 만들지 않는다. 조회에 실패한 서버는 이번엔 건너뛴다(멈춤으로 치지 않는다).
- 로그는 절대 보지 않는다 (특히 vaultwarden - 비밀번호 서버).
저장: storage/poring_food/facilities.json {"containers": {이름: {"up", "started"}}, "down_since": {이름: 시각}}
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from . import runtime, worldcfg
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_FACILITIES", "1") not in ("0", "false", "False", "")
FACILITIES_PATH = os.path.join(STATE_DIR, "facilities.json")
TIMEOUT_SEC = 60
TOWN = "마을"   # world.TOWN (순환 import 피하려고 값만)

# 서버(docker MCP 이름) -> 성벽 구역 이름
HOSTS = {
    os.getenv("PORING_DOCKER_ETC_MCP_SERVER", "docker_local"): "수비대 본부 쪽 성벽",
    os.getenv("PORING_DOCKER_PLEX_MCP_SERVER", "docker_bookoasis"): "도서관·극장 쪽 성벽",
}
# 컨테이너 -> (이야기 속 이름, 사건이 닿는 곳, 꺼졌을 때 겪는 일)
FACILITIES = {
    "litellm-proxy": ("수비대 마나 중계탑", TOWN, "수비대 주문 두루마리가 불발되고 마나 장부가 멈췄다"),
    "litellm-db": ("수비대 기록 보관소", TOWN, "마나 장부를 못 꺼낸다"),
    "docker-warproxy": ("성문 전령 초소", TOWN, "바깥으로 나가는 전령과 짐마차가 멈췄다"),
    "vaultwarden": ("마을 열쇠지기 탑", TOWN, "열쇠를 맡기거나 찾을 수가 없다"),
    "bookoasis": ("도서관 서가 감시탑", worldcfg.BOOK, "책 찾기 마법과 대출 장부가 먹통이다"),
    "bookoasis_mariadb": ("도서관 장부 금고", worldcfg.BOOK, "재고 장부를 못 연다"),
    "bookoasis_redis": ("도서관 대출대 수정구", worldcfg.BOOK, "대출대 수정구가 먹통이라 방문객을 줄 세워 놓았다"),
    "tautulli": ("환영극장 관객 세는 수정구", worldcfg.CINEMA, "관객 수 집계가 멈췄다"),
    "flaskfarm": ("환영극장 이야기 배급소", worldcfg.CINEMA, "새 환영극이 들어오지 않는다"),
}


def _load() -> dict:
    try:
        with open(FACILITIES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(FACILITIES_PATH), exist_ok=True)
    tmp = FACILITIES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FACILITIES_PATH)


# ===================================================================== 조회
def parse_containers(res) -> list[dict]:
    """list_containers 결과 -> [{"name", "up", "started"}]. structuredContent가 있으면 그걸, 없으면 글(JSON)을."""
    items = []
    structured = getattr(res, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), list):
        items = structured["result"]
    else:
        for block in getattr(res, "content", None) or []:
            try:
                v = json.loads(getattr(block, "text", "") or "")
            except (TypeError, ValueError):
                continue
            items += v if isinstance(v, list) else [v]
    out = []
    for c in items:
        if not isinstance(c, dict) or not c.get("name"):
            continue
        state = c.get("state") if isinstance(c.get("state"), dict) else {}
        health = ((state.get("Health") or {}).get("Status") or "") if isinstance(state.get("Health"), dict) else ""
        running = bool(state.get("Running")) if state else c.get("status") == "running"
        up = running and not state.get("Restarting") and health != "unhealthy"
        out.append({"name": str(c["name"]).lstrip("/"), "up": up, "started": str(state.get("StartedAt") or "")})
    return out


async def _fetch_async() -> dict[str, list[dict]]:
    import asyncio
    from ai.mcp_manager import server_session

    async def one(server: str) -> list[dict]:
        async with server_session(server) as session:
            return parse_containers(await session.call_tool("list_containers", {"all": True}))

    servers = list(HOSTS)
    results = await asyncio.gather(*(one(s) for s in servers), return_exceptions=True)
    out = {}
    for server, res in zip(servers, results):
        if isinstance(res, BaseException):
            print(f"[설비] {server} 조회 실패 - 이번엔 건너뜀: {res!r}"[:200])
        else:
            out[server] = res
    return out


def fetch() -> dict[str, list[dict]]:
    """[스레드에서] {서버: [컨테이너...]} - 실패한 서버는 빠진다."""
    if not ENABLED or not runtime.is_bound():
        return {}
    try:
        return runtime.run_on_bot_loop(_fetch_async(), timeout=TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001
        print(f"[설비] docker 조회 실패: {e!r}"[:200])
        return {}


# ===================================================================== 사건
def diff(prev: dict, seen: dict[str, list[dict]], now: datetime) -> tuple[dict, list[dict]]:
    """이전 상태와 이번 조회를 비교 -> (새 상태, 사건 목록). 사건 = {"kind": down/up/restart/blackout, "name", "title",
    "detail", "targets"}. 순수 함수 (테스트용)."""
    containers = dict(prev.get("containers") or {})
    down_since = dict(prev.get("down_since") or {})
    first = not containers
    events = []
    for server, listed in seen.items():
        building = HOSTS.get(server, server)
        by_name = {c["name"]: c for c in listed}
        mine = [n for n in FACILITIES if n in by_name or n in containers and containers[n].get("server") == server]
        restarted = []
        for name in mine:
            label, target, effect = FACILITIES[name]
            cur = by_name.get(name) or {"name": name, "up": False, "started": ""}
            old = containers.get(name)
            containers[name] = {"server": server, "up": cur["up"], "started": cur["started"] or (old or {}).get("started", "")}
            if first or old is None:
                continue
            if old.get("up") and not cur["up"]:
                down_since[name] = now.isoformat(timespec="minutes")
                events.append({"kind": "down", "name": name, "title": f"{label} 불 꺼짐",
                               "detail": f"{building} {label}의 불이 갑자기 꺼졌다 - {effect}.", "targets": [target]})
            elif not old.get("up") and cur["up"]:
                since = down_since.pop(name, "")
                events.append({"kind": "up", "name": name, "title": f"{label} 다시 불 켜짐",
                               "detail": f"{label}에 다시 불이 켜졌다" + (f" ({since[11:16]}부터 꺼져 있었다)." if since else "."),
                               "targets": [target], "resolves": f"{label} 불 꺼짐"})
            elif cur["up"] and old.get("started") and cur["started"] and cur["started"] != old["started"]:
                restarted.append(name)
        # 한 구역의 시설이 전부 새로 시작됨 = 결계 균열 하나로 묶는다
        known_up = [n for n in mine if containers[n]["up"]]
        if restarted and len(restarted) == len(known_up) and len(restarted) >= 2:
            events.append({"kind": "blackout", "name": building, "title": f"{building} 결계 균열",
                           "detail": f"{building}의 결계에 잠깐 금이 갔다 - "
                                     f"{', '.join(FACILITIES[n][0] for n in restarted)}의 불이 꺼졌다가 다시 켜졌다.",
                           "targets": sorted({FACILITIES[n][1] for n in restarted})})
        else:
            for name in restarted:
                label, target, _ = FACILITIES[name]
                events.append({"kind": "restart", "name": name, "title": f"{label} 잠깐 꺼짐",
                               "detail": f"{label}의 불이 잠깐 꺼졌다가 다시 켜졌다 (마나가 끊겼었나?).", "targets": [target]})
    return {"containers": containers, "down_since": down_since, "at": now.isoformat(timespec="minutes")}, events


def check(now: datetime | None = None, fetcher=None, inject=None) -> list[dict]:
    """[매시 회차 시작, 스레드에서] 조회 -> 비교 -> 사건. inject(targets, detail, title)는 agents.inject_event.
    반환: 이번에 생긴 사건."""
    if not ENABLED:
        return []
    now = now or now_kst()
    seen = (fetcher or fetch)()
    if not seen:
        return []
    prev = _load()
    state, events = diff(prev, seen, now)
    _save(state)
    if not prev.get("containers"):
        print(f"[설비] 처음 조회 - 기준만 기록 ({sum(len(v) for v in seen.values())}개 컨테이너)")
        return []
    if inject is None:
        from .agents import inject_event as inject
    from . import chronicle
    for e in events:
        try:
            got = inject(e["targets"], e["detail"], e["title"])
        except Exception as ex:  # noqa: BLE001 - 사건 하나가 실패해도 나머지는 간다
            print(f"[설비] 사건 전달 실패 ({e['title']}): {ex}")
            continue
        if e.get("resolves"):
            chronicle.update_thread(e["resolves"], e["detail"], resolved=True, resolution=e["detail"])
        print(f"[설비] {e['title']} -> {', '.join(e['targets'])} ({len(got or [])}명)")
    return events


def prompt_block() -> str:
    """판단용: 지금 불 꺼진 감시탑/시설 (성벽 일이라 모두가 안다)."""
    data = _load()
    down = [(n, s) for n, s in (data.get("down_since") or {}).items() if n in FACILITIES]
    if not down:
        return ""
    return "[지금 불 꺼진 감시탑/시설 - 성벽 사정]\n" + "\n".join(
        f"- {FACILITIES[n][0]} ({s[5:16].replace('T', ' ')}부터): {FACILITIES[n][2]}" for n, s in down)
