"""
텔레그램 자동 올리기 대기열 - bookoasis_assistant(BOA)의 텔레그램 자동 올리기가 지금 무엇을 올리고 무엇이 줄 서 있는지 디스코드에서 본다.

읽는 곳: BOA MCP의 tgup_status 도구 (읽기 전용) - 원고 입고 대기열(gdw_queue)과 같은 주소·토큰
  PORING_ASSISTANT_URL, PORING_ASSISTANT_MCP_TOKEN
쓰는 곳: LLM 도구 get_telegram_upload_queue ("텔레그램 올리기 대기열 보여줘", "○○ 올라갔어?").
유튜브 보내기: /유튜브 [관리자] - BOA MCP의 tgup_youtube 도구 (쓰기, BOA 2.72.1+) - 링크를 받아 유튜브 규칙의 텔레그램 채널에 올린다.
"""
from __future__ import annotations

import re

from . import gdw_queue
from ._log import pf_print as print  # print()를 봇 로그로


def fetch(limit: int = 30, timeout: int = 10) -> dict | None:
    """tgup_status 그대로 (못 읽으면 None)"""
    if not gdw_queue.enabled():
        return None
    try:
        return gdw_queue.call_tool("tgup_status", {"limit": limit}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"[텔레그램 올리기] 대기열을 읽지 못함: {e}")
        return None


def _mins(m) -> str:
    if m is None:
        return ""
    m = int(m)
    return f"{m // 60}시간 {m % 60}분" if m >= 60 else f"{m}분"


def text(data: dict | None, limit: int = 15) -> str:
    """tgup_status -> 디스코드에 보일 글"""
    if not gdw_queue.enabled():
        return "📤 텔레그램 자동 올리기: 연결 안 됨 (PORING_ASSISTANT_URL · PORING_ASSISTANT_MCP_TOKEN)"
    if not data:
        return "📤 텔레그램 자동 올리기: 읽지 못했습니다 (bookoasis_assistant 주소·MCP 토큰, BOA 버전 2.67.1 이상인지 확인)"
    out = [f"📤 **텔레그램 자동 올리기** - 올리는 중 {len(data.get('running') or [])}개 · 대기 {data.get('queue_total', 0)}개"
           + (f" · 자막 기다림 {len(data['waiting_subs'])}개" if data.get("waiting_subs") else "")
           + (f" · 다시 시도 {len(data['retry'])}개" if data.get("retry") else "")]
    if data.get("off_hours"):
        out.append("• ⏸ 지금은 올리는 시간대가 아님 (규칙의 시간대 설정)")
    if data.get("error"):
        out.append(f"• ⚠️ {data['error'][:160]}")
    for w in data.get("throttle_warnings") or []:   # 텔레그램 업로드 속도 제한 (BOA 2.69.0+)
        out.append(f"• 🐢 {w[:200]}")
    for d in data.get("offline_drives") or []:
        out.append(f"• ⚠️ 드라이브 끊김: {d['drive']} ({_mins(d.get('since_min'))} 전부터)")
    for r in data.get("running") or []:
        pct = f" {r['percent']:.0f}%" if r.get("percent") is not None else ""
        out.append(f"• ▶ [{r['account']}] {r['title']} - {r['phase']}{pct} · {_mins(round(r.get('elapsed_min') or 0))}째"
                   + (f" → {r['channel']}" if r.get("channel") else ""))
    speeds = [f"{name} {u['speed']}MB/s" + (f"(평소 {u['baseline']})" if u.get("baseline") else "")
              for name, a in (data.get("accounts") or {}).items() for u in [a.get("upload") or {}] if u.get("speed") is not None]
    if speeds:
        out.append("• 업로드 속도: " + " · ".join(speeds))
    queue = data.get("queue") or []
    if queue:
        out.append("**대기열**")
        out += [f"{q['pos']}. {q['title']}" + (f" → {q['channel']}" if q.get("channel") else "") for q in queue[:limit]]
        more = int(data.get("queue_total") or 0) - min(limit, len(queue))
        if more > 0:
            out.append(f"… 외 {more}개")
    if data.get("waiting_subs"):
        out.append("**자막 기다림**")
        out += [f"• {w['title']}" + (f" ({w['why']})" if w.get("why") else "") + (f" · {_mins(w.get('waiting_min'))}째" if w.get("waiting_min") else "")
                for w in data["waiting_subs"][:5]]
    if data.get("retry"):
        out.append("**다시 시도 대기**")
        out += [f"• {r['title']} ({r.get('tries', 0)}번 실패 · {_mins(r.get('in_min'))} 뒤)" for r in data["retry"][:5]]
    recent = data.get("recent") or []
    if recent:
        out.append("**최근 기록**")
        out += [f"• {'✅' if h['result'] == '올림' else '⏭️' if h['result'].startswith('건너뜀') else '❌'} {h['title']}"
                + (f" - {h['note'][:80]}" if h['result'] != '올림' and h.get('note') else "")
                + (f" ({_mins(h.get('ago_min'))} 전)" if h.get("ago_min") is not None else "") for h in recent[:5]]
    return "\n".join(out)


YT_HINT = re.compile(r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)/\S+", re.I)


def youtube(urls: str, rule: str = "", again: bool = False, timeout: int = 200) -> str:
    """유튜브 링크들 -> BOA 텔레그램 자동 올리기의 유튜브 규칙 진행 목록에 (tgup_youtube). 디스코드에 보일 글"""
    if not gdw_queue.enabled():
        return "📤 텔레그램 자동 올리기: 연결 안 됨 (PORING_ASSISTANT_URL · PORING_ASSISTANT_MCP_TOKEN)"
    links = YT_HINT.findall(urls or "")
    if not links:
        return "▶️ 유튜브 영상·재생목록 주소를 넣어 주세요 (youtube.com / youtu.be)."
    try:
        r = gdw_queue.call_tool("tgup_youtube", {"urls": "\n".join(links), "rule": rule, "again": again}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "tgup_youtube" in msg and "유튜브 규칙" not in msg:   # BOA가 이 도구를 모름 (예전 버전)
            msg += " (BOA를 2.72.1 이상으로 업데이트해야 합니다)"
        return f"⚠️ 유튜브 보내기 실패: {msg}"
    if not r.get("queued"):
        return f"▶️ 이미 진행 목록에 있습니다 → {r.get('channel', '')} (진행은 /올리기)"
    return (f"▶️ 유튜브 {r['queued']}개를 진행 목록에 넣었습니다 → **{r.get('channel', '')}**"
            + (f" (이미 있던 {r['already_queued']}개 빼고)" if r.get("already_queued") else "")
            + ("\n⚠️ 이 유튜브 규칙은 꺼져 있습니다 - BOA 규칙에서 켜 주세요." if r.get("enabled") is False else "")
            + "\n차례가 되면 받아서 올립니다 - 진행은 /올리기")
