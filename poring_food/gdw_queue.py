"""
원고 입고 대기열 - bookoasis_assistant의 드라이브 변경 감시(GDW)에 밀린 일이 얼마나 있는지 인물 세계와 채널에 보여 준다.

읽는 곳: bookoasis_assistant의 MCP(POST <주소>/mcp, Bearer 토큰)의 gdw_status 도구 (읽기 전용).
  PORING_ASSISTANT_URL        예: http://192.168.0.31:5931   (비우면 이 기능은 꺼짐)
  PORING_ASSISTANT_MCP_TOKEN  bookoasis_assistant [설정 > MCP]에서 만든 토큰
보여 주는 방법 셋:
  1) 담당자의 지금 상태 (stewards.status_line("assistant")) - "입고 대기 12건(가장 오래된 것 35분) · 실패 3건"이 붙어
     원고 운반 담당이 매시 판단할 때 알고, 대화에 자연스럽게 나온다.
  2) 채널 알림 (check) - 대기열이 밀리거나(LEVELS) 실패가 쌓이거나 워커가 멈추면 한 번, 풀리면 한 번. 같은 단계에서는 다시 알리지 않는다.
  3) 물어보면 (summary) - 서버 조절 요약처럼 지금 숫자를 그대로.
이야기: 편집국 원고 입고 대기 줄. 원고 운반 담당(stewards 'assistant')과 서가 담당('bookoasis')이 바로 반응한다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

import requests

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로
from .config import STATE_DIR

URL = os.getenv("PORING_ASSISTANT_URL", "").strip().rstrip("/")
TOKEN = os.getenv("PORING_ASSISTANT_MCP_TOKEN", "").strip()
CHECK_MIN = max(1, int(os.getenv("PORING_GDW_QUEUE_CHECK_MIN", "5") or 5))
# (단계, 대기 건수, 가장 오래 기다린 분) - 둘 중 하나라도 넘으면 그 단계
LEVELS = [(1, int(os.getenv("PORING_GDW_QUEUE_WARN", "30") or 30), 60), (2, int(os.getenv("PORING_GDW_QUEUE_HIGH", "150") or 150), 180)]
FAIL_WARN = int(os.getenv("PORING_GDW_QUEUE_FAIL", "10") or 10)
NAME = "📦 원고 입고"
CACHE_SEC = 60
STATE_PATH = os.path.join(STATE_DIR, "gdw_queue.json")
_cache = {"at": 0.0, "data": None}


def enabled() -> bool:
    return bool(URL and TOKEN)


def fetch(timeout: int = 8) -> dict | None:
    """gdw_status 그대로 (못 읽으면 None). CACHE_SEC 동안 다시 묻지 않는다."""
    if not enabled():
        return None
    if _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SEC:
        return _cache["data"]
    try:
        res = requests.post(URL + "/mcp", timeout=timeout, headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "gdw_status", "arguments": {}}})
        body = res.json()
        result = body.get("result") or {}
        if body.get("error") or result.get("isError"):
            raise RuntimeError(str((body.get("error") or {}).get("message") or "도구 오류")[:120])
        data = result.get("structuredContent")
        if data is None:
            data = json.loads(next((c.get("text") for c in result.get("content") or [] if c.get("type") == "text"), "") or "{}")
    except Exception as e:  # noqa: BLE001
        print(f"[원고 입고] 대기열을 읽지 못함: {e}")
        return None
    _cache.update(at=time.time(), data=data)
    return data


def digest(status: dict | None, now: datetime | None = None) -> dict | None:
    """gdw_status → 보여 줄 숫자: 대기(모으는 중·차례 기다림·파일/BookOasis 기다림), 실패, 가장 오래 기다린 분, 워커가 도는지, 단계"""
    if not isinstance(status, dict):
        return None
    now = now or now_kst()
    counts = status.get("counts") or {}
    det = status.get("pending_detail") or {}
    pending = int(counts.get("pending") or 0) + int(counts.get("waiting") or 0)
    failed = int(counts.get("failed") or 0) + int(counts.get("timeout") or 0)
    oldest_min = 0
    if det.get("oldest"):
        try:
            oldest_min = max(0, int((now.replace(tzinfo=None) - datetime.fromisoformat(str(det["oldest"])[:19])).total_seconds() // 60))
        except ValueError:
            oldest_min = 0
    alive = bool((status.get("worker") or {}).get("alive"))
    level = 0
    for lv, n, minutes in LEVELS:
        if pending >= n or (pending and oldest_min >= minutes):
            level = lv
    return {"pending": pending, "failed": failed, "oldest_min": oldest_min, "alive": alive, "level": level,
            "buffering": int(det.get("buffering") or 0), "ready": int(det.get("ready") or 0) + int(det.get("screening") or 0),
            "waiting": int(det.get("waiting") or 0), "activity": str((status.get("worker") or {}).get("activity") or "")[:60],
            "warnings": [str(w)[:160] for w in (status.get("warnings") or [])][:3]}


def _age(minutes: int) -> str:
    return f"{minutes // 60}시간 {minutes % 60}분" if minutes >= 60 else f"{minutes}분"


def line(d: dict | None) -> str:
    """한 줄 (담당자 상태·요약용). 읽지 못했으면 빈 글"""
    if not d:
        return ""
    if not d["alive"]:
        return "입고 워커가 멈춰 있음" + (f" · 대기 {d['pending']}건" if d["pending"] else "")
    bits = [f"입고 대기 {d['pending']}건" + (f"(가장 오래된 것 {_age(d['oldest_min'])})" if d["pending"] and d["oldest_min"] else "")
            if d["pending"] else "입고 대기 없음"]
    if d["failed"]:
        bits.append(f"실패 {d['failed']}건")
    return " · ".join(bits)


def summary(now: datetime | None = None) -> str:
    if not enabled():
        return "📦 원고 입고 대기열: 꺼져 있음 (PORING_ASSISTANT_URL · PORING_ASSISTANT_MCP_TOKEN)"
    d = digest(fetch(), now)
    if not d:
        return "📦 원고 입고 대기열: 읽지 못했습니다 (bookoasis_assistant 주소·MCP 토큰 확인)"
    out = [f"📦 **원고 입고 대기열** - {line(d)}"]
    if d["pending"]:
        out.append(f"• 모으는 중 {d['buffering']} · 처리 차례 {d['ready']} · 파일·서가 기다림 {d['waiting']}"
                   + (f" · 지금 하는 일: {d['activity']}" if d["activity"] else ""))
    out += [f"• ⚠️ {w}" for w in d["warnings"]]
    return "\n".join(out)


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, STATE_PATH)


def notice(d: dict, prev: dict) -> tuple[str, str] | None:
    """알릴 일이 있으면 (이야기 글, 사건 제목). 같은 단계·같은 문제는 다시 알리지 않는다 (순수 함수)"""
    was = {"level": int(prev.get("level") or 0), "dead": bool(prev.get("dead")), "fail": bool(prev.get("fail"))}
    dead, fail = not d["alive"], d["failed"] >= FAIL_WARN
    if dead and not was["dead"]:
        return (f"편집국 원고 입고 워커가 멈췄다. 기다리는 원고가 {d['pending']}건 있다. 누가 다시 켜 줘야 한다.", "원고 입고 워커 멈춤")
    if was["dead"] and not dead:
        return (f"원고 입고 워커가 다시 돌기 시작했다. 밀린 원고 {d['pending']}건을 차례로 넘긴다.", "원고 입고 워커 재가동")
    if d["level"] > was["level"]:
        word = "많이 밀렸다" if d["level"] >= 2 else "밀리기 시작했다"
        return (f"편집국 원고 입고 줄이 {word}: 대기 {d['pending']}건, 가장 오래된 원고는 {_age(d['oldest_min'])}째 기다리는 중"
                + (f", 실패 {d['failed']}건" if d["failed"] else "") + ". 급한 원고부터 살피고, 막힌 곳이 있는지 본다.",
                f"원고 입고 대기 {d['pending']}건")
    if fail and not was["fail"]:
        return (f"원고 입고에서 실패가 {d['failed']}건 쌓였다. 같은 곳에서 계속 막히는지 확인이 필요하다.", f"원고 입고 실패 {d['failed']}건")
    if was["level"] and d["level"] == 0 and not dead:
        return ("밀렸던 원고 입고 줄이 풀렸다. 서가에 차례로 꽂혔다.", "원고 입고 대기 해소")
    return None


def check(now: datetime | None = None, post=None, react=None) -> str:
    """[CHECK_MIN분마다, 스레드에서] 대기열을 보고 달라진 것이 있으면 채널과 인물 세계에 알린다. 반환: 로그 한 줄 (없으면 빈 글)"""
    if not enabled():
        return ""
    now = now or now_kst()
    d = digest(fetch(), now)
    if not d:
        return ""
    state = _load()
    got = notice(d, state)
    state.update(level=d["level"], dead=not d["alive"], fail=d["failed"] >= FAIL_WARN, at=now.isoformat(timespec="minutes"), last=d)
    _save(state)
    if not got:
        return ""
    text, title = got
    from . import agents as ag, stewards
    (post or (lambda t: ag._post_as(NAME, t)))(f"-# {NAME} · {now:%H:%M} · {line(d)}\n{text}")
    try:
        people = list(dict.fromkeys([stewards.steward("assistant"), stewards.steward("bookoasis"), stewards.steward("chair")]))
        (react or ag.react_now)(text, title, people)
    except Exception as e:  # noqa: BLE001
        print(f"[원고 입고] 세계 반영 실패: {e}")
    return title
