"""
하루 운영 요약 - 매일 OPS_DIGEST_HOUR(9)시에 지난 하루 서버가 어땠는지 ⚙️ 운영 채널에 한 번 (LLM 없음, 숫자만).

  서비스     응답 확인(uptime): 지금 상태, 오늘 자동 복구 횟수, 멈춰 있던 시간
  채우기     채우기 운영(fill): 지금 모드, 마지막으로 한 일
  웹툰       BOA 다운로드 이력(wtm_history): 지난 24시간 받은 회차 수 (네이버/카카오)
  텔레그램   BOA 자동 올리기(tgup_status): 진행 목록·다시 시도·자막 기다림, 최근 기록 중 지난 24시간 올림/실패
  원고 입고  드라이브 감시 대기열(gdw_queue): 대기·실패
  업데이트   앱 업데이트(updater): 지난 24시간 업데이트·실패, 미뤄 둔 새 버전
  운영자     LLM 운영자(ops_agent): 지난 24시간 진단 수
못 읽은 칸은 "못 읽음"으로 두고 나머지는 그대로 보낸다.
저장: storage/poring_food/ops_digest.json {"day": 마지막으로 보낸 날}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로
from .config import STATE_DIR

ENABLED = os.getenv("OPS_DIGEST", "1") not in ("0", "false", "False", "")
HOUR = int(os.getenv("OPS_DIGEST_HOUR", "9"))
STATE_PATH = os.path.join(STATE_DIR, "ops_digest.json")
NAME = "⚙️ 운영"


def due(now: datetime) -> bool:
    if not ENABLED or now.hour != HOUR:
        return False
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return (json.load(f) or {}).get("day") != now.strftime("%Y-%m-%d")
    except (OSError, ValueError):
        return True


def mark(now: datetime) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"day": now.strftime("%Y-%m-%d")}, f)


def _boa(name: str, args: dict | None = None):
    from . import gdw_queue
    return gdw_queue.call_tool(name, args or {}, timeout=20)


def _safe(fn, label: str) -> str:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"[운영 요약] {label} 못 읽음: {e}")
        return f"**{label}** 못 읽음"


def services(now: datetime) -> str:
    from . import uptime
    svc = uptime._load().get("svc") or {}
    today = now.strftime("%Y-%m-%d")
    parts = []
    for t in uptime.targets():
        if not t["url"]:
            continue
        s = svc.get(t["name"]) or {}
        state = "❌ 응답 없음" if s.get("fails", 0) >= uptime.FAIL_N else "✅"
        extra = []
        if s.get("restart_day") == today and s.get("restarts"):
            extra.append(f"자동 복구 {s['restarts']}번")
        if s.get("down_day") == today and s.get("down_min"):
            extra.append(f"멈춤 약 {s['down_min']}분")
        parts.append(f"{t['name']} {state}" + (f" ({', '.join(extra)})" if extra else ""))
    return "**서비스** " + (" · ".join(parts) if parts else "확인하는 곳 없음")


def filling() -> str:
    from . import fill
    t = fill.status_text().split("\n")
    return "**채우기** " + t[0].replace("⚙️ **채우기 운영** - ", "") + (" · " + " · ".join(x.lstrip("• ") for x in t[1:]) if len(t) > 1 else "")


def webtoon(now: datetime, boa=None) -> str:
    hist = (boa or _boa)("wtm_history", {"limit": 500}) or []
    since = (now - timedelta(hours=24)).timestamp()
    got = [h for h in hist if isinstance(h, dict) and h.get("type") in ("download", "manual_download") and float(h.get("ts") or 0) >= since]
    kakao = sum(1 for h in got if str(h.get("platform") or "").startswith("kakao"))
    fails = sum(1 for h in hist if isinstance(h, dict) and "fail" in str(h.get("type") or "") and float(h.get("ts") or 0) >= since)
    return f"**웹툰** 지난 24시간 {len(got)}화 받음 (네이버 {len(got) - kakao} · 카카오 {kakao})" + (f" · 실패 {fails}" if fails else "")


def telegram(boa=None) -> str:
    st = (boa or _boa)("tgup_status", {"limit": 20}) or {}
    recent = [r for r in st.get("recent") or [] if (r.get("ago_min") or 0) <= 24 * 60]
    ok = sum(1 for r in recent if r.get("result") == "올림")
    line = (f"**텔레그램** 진행 목록 {st.get('queue_total', 0)} · 다시 시도 {len(st.get('retry') or [])} · "
            f"자막 기다림 {len(st.get('waiting_subs') or [])} · 최근 기록 중 24시간 안 올림 {ok}/실패 {len(recent) - ok}")
    if st.get("throttle_warnings"):
        line += " · ⚠️ 속도 제한 " + "; ".join(st["throttle_warnings"])[:120]
    return line


def manuscripts() -> str:
    from . import gdw_queue
    d = gdw_queue.digest(gdw_queue.fetch()) or {}
    if not d:
        return "**원고 입고** 못 읽음"
    return f"**원고 입고** 대기 {d.get('pending', 0)} · 실패 {d.get('failed', 0)}" + ("" if d.get("alive", True) else " · ⚠️ 워커 멈춤")


def updates(now: datetime) -> str:
    from . import updater
    st = updater._state()
    since = (now - timedelta(hours=24)).isoformat(timespec="minutes")
    done, failed, waiting = [], [], []
    for key, a in st.items():
        label = (updater.APPS.get(key) or {}).get("label", key)
        for h in a.get("history") or []:
            if h.get("at", "") >= since:
                (done if h.get("ok") else failed).append(f"{label} {h.get('to', '')}")
        p = a.get("pending") or {}
        if p and not p.get("update"):
            waiting.append(label)
    parts = []
    if done:
        parts.append("업데이트 " + ", ".join(done))
    if failed:
        parts.append("⚠️ 실패 " + ", ".join(failed))
    if waiting:
        parts.append("관리자 확인 기다림 " + ", ".join(waiting))
    return "**업데이트** " + (" · ".join(parts) if parts else "없음")


def operator(now: datetime) -> str:
    from . import ops_agent
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    n = sum(1 for h in ops_agent._load().get("history") or [] if h.get("at", "") >= since)
    return f"**운영자 진단** {n}건" if n else ""


def build(now: datetime | None = None, boa=None) -> str:
    now = now or now_kst()
    lines = [f"-# ⚙️ 하루 운영 요약 · {now:%m/%d %H:%M}",
             _safe(lambda: services(now), "서비스"), _safe(filling, "채우기"),
             _safe(lambda: webtoon(now, boa), "웹툰"), _safe(lambda: telegram(boa), "텔레그램"),
             _safe(manuscripts, "원고 입고"), _safe(lambda: updates(now), "업데이트"), _safe(lambda: operator(now), "운영자")]
    return "\n".join(x for x in lines if x)


def run(now: datetime | None = None, post=None) -> bool:
    """[스레드에서] 보낼 때면 보내고 True"""
    now = now or now_kst()
    if not due(now):
        return False
    text = build(now)
    if post is None:
        from .agents import _post_as
        post = lambda t: _post_as(NAME, t)   # noqa: E731
    post(text)
    mark(now)
    return True
