"""
다운로드 에이전트 - bookoasis_assistant(BOA)의 웹툰·웹소설 다운로드와 텔레그램 자동 올리기 일정·계획을 LLM이 1시간마다 맡는다.

목표: 영상 재생·책 읽기를 방해하지 않고(서버 조절 단계) 사이트에 막히지 않는 선에서 최대한 많이 받고 올리기.
- 볼 것 (BOA MCP, 읽기): wtm_status·wtm_forecast·wtm_settings·wtm_titles·wtm_title_report·tgup_status·tgup_settings + 서버 조절 단계·재생 수
- 바꿀 것 (BOA MCP, 쓰기): wtm_set_settings(일정·동시 수…), wtm_set_subscription(구독), wtm_run(스캔·전체 실행·취소),
  tgup_set_settings(올리는 시간대·동시 인코딩·규칙)
- 안전 범위(guard) 안이면 바로 적용하고 채널에 무엇을 왜 바꿨는지 알린다. 범위 밖이면 적용하지 않고 '제안'으로 알린다
  (관리자가 BOA 화면에서 직접). 경로·쿠키·알림 칸은 BOA가 처음부터 잠가 둠.
- 기록: storage/poring_food/boa_agent.json - 최근 판단(요약·바꾼 것·제안)을 다음 판단에 같이 넣어 왔다 갔다 하지 않게.

설정 (.env): BOA_AGENT=0 이면 끔 (기본 켬 - PORING_ASSISTANT_URL·PORING_ASSISTANT_MCP_TOKEN이 있을 때), BOA_AGENT_EVERY_MIN(기본 60),
  BOA_AGENT_MODEL: 판단에 쓸 LiteLLM model_name (예: 추론 모델 nemotron-3-chat). 비우면 봇 기본 모델(LITELLM_MODEL).
  그 모델이 실패하면(무료 한도·도구 호출 미지원·오류) 그 판단은 기본 모델로 다시 한다. BOA_AGENT_MAX_TOKENS(기본 8000)
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로
from .config import STATE_DIR
from . import gdw_queue

ENABLED = os.getenv("BOA_AGENT", "1") not in ("0", "false", "False", "")
EVERY_MIN = max(15, int(os.getenv("BOA_AGENT_EVERY_MIN", "60") or 60))
MODEL = os.getenv("BOA_AGENT_MODEL", "").strip()
MAX_TOKENS = max(1024, int(os.getenv("BOA_AGENT_MAX_TOKENS", "8000") or 8000))
STATE_PATH = os.path.join(STATE_DIR, "boa_agent.json")
NAME = "🤖 다운로드 에이전트"
MAX_TURNS = 12
KEEP = 24

READ_TOOLS = {
    "wtm_status": "웹툰 다운로더 현황 (진행 중인 작업·카카오 대기열·속도·최근 로그)",
    "wtm_forecast": "앞으로 며칠 날마다 받을 회차 수·용량 예측과 최근 날짜별 받은 양. 인자 days(1~90)",
    "wtm_settings": "웹툰 다운로더 설정 전체 (key·값·설명·다음 자동 실행). locked 칸은 못 바꿈",
    "wtm_titles": "작품 목록. 인자 query·state(all/subscribed/unsubscribed/excluded)·platform(all/naver/kakao)·limit",
    "wtm_title_report": "작품 하나의 다운로드 현황·일정. 인자 query",
    "tgup_status": "텔레그램 자동 올리기 현황 (올리는 중·대기열·자막 기다림·다시 시도·최근 기록). 인자 limit",
    "tgup_settings": "텔레그램 자동 올리기 설정 (시간대·동시 인코딩·규칙들)",
}
WRITE_TOOLS = {
    "wtm_set_settings": "웹툰 다운로더 설정 바꾸기. 인자 values={key: 값}",
    "wtm_set_subscription": "작품 구독 바꾸기. 인자 titleId, platform(naver/kakao), state(subscribe/unsubscribe/exclude/restore)",
    "wtm_run": "웹툰 다운로더 작업: job = scan_now / run_full_cycle_now / scan_finished_now / cancel_job",
    "tgup_set_settings": "텔레그램 자동 올리기 설정 바꾸기. 인자 settings={공통 칸}, rules=[{id, 칸…}], add_rules=[새 규칙]",
}

# ── 안전 범위 ──
WTM_HUMAN_ONLY = {   # 돈·취향·작업 방식 - 사람이 정한다 (BOA 잠금 칸 말고도)
    "KAKAO_USE_PAID_TICKETS": "유료 이용권 쓰기(돈)", "ALLOW_BL": "BL 받기(취향)", "ALLOW_GL": "GL 받기(취향)",
    "HIDE_BL_GL": "BL·GL 숨기기(취향)", "WORKER_MODE": "작업 프로세스 방식", "LOW_PRIORITY_MODE": "CPU 양보 끄기",
    "AUTO_SUBSCRIBE_NEW_TITLES": "신간 전부 자동 구독",
}
LIMITS = {   # 칸: (최소, 최대) - 서버 경보 2단계 이상이면 아래 STAGE_CAPS
    "PARALLEL_TITLES": (1, 10), "MAX_CONCURRENT_DOWNLOADS": (1, 16), "DELAY_SECONDS": (0.2, 10),
    "MAX_NEW_EPISODES_PER_TITLE": (1, 50), "WAITFREE_CHECK_MINUTES": (3, 60), "REQUEST_TIMEOUT_SECONDS": (5, 60),
    "INITIAL_EPISODES_LIMIT": (0, 200), "FINISHED_SCAN_HOUR": (0, 23), "WAITFREE_PLAN_HOUR": (0, 23),
}
# 서버 경보 단계별 속도 상한 (동시 작품, 동시 이미지, 회차 간 대기 최소) - 부하가 높으면 멈추지 않고 이만큼 늦춘다.
# 채우기 운영(fill)이 규칙으로 5분마다 맞추고 한가해지면 되돌린다. 에이전트의 쓰기도 이 상한을 넘지 못한다
STAGE_CAPS = {2: (3, 8, 0.2), 3: (2, 6, 1.0), 4: (1, 4, 2.0)}
# 끄지 않는 칸 - 부하가 높으면 끄지 말고 속도를 낮춘다 (끄는 것은 관리자만)
NEVER_OFF = {"ENABLE_SCHEDULER": "자동 실행", "KAKAO_ENABLE": "카카오 받기"}


def stage_caps(stage: int):
    """경보 단계 -> (동시 작품 최대, 동시 이미지 최대, 회차 간 대기 최소) 또는 None (2단계 미만)"""
    if stage is None or stage < 2:
        return None
    return STAGE_CAPS[min(int(stage), max(STAGE_CAPS))]


def _off(v) -> bool:
    return v in (False, "false", "False", "0", 0, None, "off")
# 관리자가 올려 둔 속도(동시 작품·이미지 수, 고속 모드)는 서버가 한가할 때(경보 2단계 미만) 낮추지 않는다 - 제안으로만
SPEED_KEYS = ("PARALLEL_TITLES", "MAX_CONCURRENT_DOWNLOADS")
TGUP_LIMITS = {"encode_slots": (1, 2), "interval": (5, 1440)}
MAX_SUBSCRIPTION_CHANGES = 10   # 한 번 판단에 구독 바꾸기
MAX_NEW_RULES = 2
RUN_GAP_SEC = 2 * 3600          # 스캔·전체 실행은 2시간에 한 번까지 (자동 실행과 별개로 몰아 돌리지 않게)
CRON_MIN_GAP_MIN = 30           # 자동 실행 간격 최소


def enabled() -> bool:
    return ENABLED and gdw_queue.enabled()


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


def _cron_gap_ok(expr: str) -> str:
    """자동 실행 crontab이 너무 잦지 않은지 (분 칸이 */N(N<30)·여러 값이면서 시 칸이 매시) - 문제면 까닭, 아니면 ''"""
    parts = str(expr or "").split()
    if len(parts) != 5:
        return ""   # (형식은 BOA가 검사)
    minute, hour = parts[0], parts[1]
    if minute == "*":
        return "매분 실행"
    if minute.startswith("*/"):
        try:
            if int(minute[2:]) < CRON_MIN_GAP_MIN:
                return f"{minute[2:]}분마다 실행 (최소 {CRON_MIN_GAP_MIN}분)"
        except ValueError:
            return ""
    if "," in minute and len(minute.split(",")) > 2 and hour in ("*", "*/1"):
        return "한 시간에 세 번 이상 실행"
    return ""


def guard(name: str, args: dict, stage: int, state: dict, now: float | None = None) -> str:
    """이 쓰기를 바로 해도 되는지 - 안 되면 까닭(제안으로 남김), 되면 ''"""
    now = now or time.time()
    if name == "wtm_set_settings":
        values = args.get("values") if isinstance(args.get("values"), dict) else {}
        if not values:
            return "values가 비어 있음"
        for k, v in values.items():
            if k in WTM_HUMAN_ONLY:
                return f"{k}({WTM_HUMAN_ONLY[k]})는 관리자가 정함"
            if k in NEVER_OFF and _off(v):
                return (f"{k}({NEVER_OFF[k]})는 끄지 않음 - 서버가 바쁘면 멈추지 말고 속도를 낮춘다 "
                        "(경보 단계에 맞춰 채우기 운영이 자동으로 낮추고 한가해지면 되돌림)")
            if k == "FAST_MODE" and v in (True, "true", "1", 1) and stage >= 2:
                return f"서버 경보 {stage}단계라 고속 모드는 켜지 않음"
            cur = state.get("wtm_current") or {}
            if stage < 2 and k == "FAST_MODE" and v in (False, "false", "0", 0, None) and cur.get("FAST_MODE") in (True, "true", "1", 1):
                return "서버가 한가할 때(경보 2단계 미만)는 관리자가 켠 고속 모드를 끄지 않음 - 사이트 제한(429/503)이 원인이면 제안으로"
            if stage < 2 and k in SPEED_KEYS and cur.get(k) is not None:
                try:
                    if float(v) < float(cur[k]):
                        return (f"서버가 한가할 때(경보 2단계 미만)는 관리자가 정한 {k}({cur[k]})를 낮추지 않음 - "
                                "사이트 제한(429/503)이 원인이면 제안으로")
                except (TypeError, ValueError):
                    pass
            if k == "SCHEDULE_CRON":
                why = _cron_gap_ok(v)
                if why:
                    return f"자동 실행이 너무 잦음: {why}"
            if k in LIMITS:
                try:
                    num = float(v)
                except (TypeError, ValueError):
                    return f"{k}는 숫자여야 함"
                lo, hi = LIMITS[k]
                caps = stage_caps(stage)
                if caps:
                    hi = min(hi, {"PARALLEL_TITLES": caps[0], "MAX_CONCURRENT_DOWNLOADS": caps[1]}.get(k, hi))
                    lo = max(lo, caps[2]) if k == "DELAY_SECONDS" else lo
                if not lo <= num <= hi:
                    return f"{k}={v}는 안전 범위({lo}~{hi}{f', 서버 경보 {stage}단계' if caps else ''}) 밖"
        return ""
    if name == "tgup_set_settings":
        st = args.get("settings") if isinstance(args.get("settings"), dict) else {}
        for k, (lo, hi) in TGUP_LIMITS.items():
            if k in st:
                if k == "encode_slots" and stage >= 2:
                    hi = 1
                try:
                    if not lo <= float(st[k]) <= hi:
                        return f"{k}={st[k]}는 안전 범위({lo}~{hi}) 밖"
                except (TypeError, ValueError):
                    return f"{k}는 숫자여야 함"
        if len(args.get("add_rules") or []) > MAX_NEW_RULES:
            return f"새 규칙은 한 번에 {MAX_NEW_RULES}개까지"
        return ""
    if name == "wtm_set_subscription":
        if int(state.get("subs_this_run", 0)) >= MAX_SUBSCRIPTION_CHANGES:
            return f"구독 바꾸기는 한 번에 {MAX_SUBSCRIPTION_CHANGES}개까지"
        return ""
    if name == "wtm_run":
        job = args.get("job")
        if job == "cancel_job":
            if stage >= 2:
                return f"서버 경보 {stage}단계라도 작업을 멈추지 않음 - 속도는 채우기 운영이 경보 단계에 맞춰 낮춤"
            return ""
        if job not in ("scan_now", "run_full_cycle_now", "scan_finished_now"):
            return "알 수 없는 작업"
        if stage >= 2:
            return f"서버 경보 {stage}단계라 지금 몰아 돌리지 않음"
        last = float((state.get("last_run") or {}).get(job) or 0)
        if now - last < RUN_GAP_SEC:
            return f"{job}은 {RUN_GAP_SEC // 3600}시간에 한 번까지 (마지막 {int((now - last) // 60)}분 전)"
        return ""
    return "알 수 없는 도구"


SYSTEM = """너는 집 서버의 '다운로드 에이전트'다. bookoasis_assistant(BOA)의 웹툰·웹소설 다운로더와 텔레그램 자동 올리기의
일정과 계획을 1시간마다 살펴 조정한다.

목표 (앞의 것이 우선):
1) 영상 재생·책 읽기에 불편이 없게 - 서버 경보 단계가 높거나 재생 중이면 무거운 일(고속 모드·동시 수 늘리기·몰아 돌리기·동시 인코딩)을 줄이거나 미룬다.
   웹툰 다운로더는 끄거나 멈추지 않는다 (ENABLE_SCHEDULER·KAKAO_ENABLE 끄기·cancel_job은 시스템이 거절함).
   경보 2단계 이상이면 채우기 운영이 규칙으로 속도를 낮추고(2단계 동시 작품 3·이미지 8, 3단계 2·6·대기 1초, 4단계~ 1·4·대기 2초, 고속 모드 끔)
   한가해지면 관리자 값으로 되돌린다 - 그래서 부하 때문에 웹툰 다운로더 설정을 따로 바꿀 필요는 없다.
   관리자가 올려 둔 속도(동시 작품·이미지 수, 고속 모드)는 서버 경보 2단계 미만이면 낮추지 않는다 (시스템이 거절함 - 사이트 제한이 원인이면 제안으로).
   기다무가 밀려 있으면(wtm_status의 job.waitfree_due가 크거나 waitfree_backlog) 속도를 줄이지 말고, 전체 실행을 따로 몰아 돌리지 않는다.
2) 사이트에 막히지 않게 - 최근 로그에 429/503·차단·쿠키 만료가 보이면 동시 수를 줄이고 회차 간 대기를 늘린다.
   텔레그램이 업로드를 늦추면(tgup_status의 throttle_warnings·accounts.upload.state=limited) 동시 인코딩을 늘리지 말고,
   제한이 오래가면 그 계정이 덜 쓰이게(규칙의 tg_alt로 다른 계정을 같이 쓰게) 하는 것을 생각한다.
3) 그 안에서 웹툰·웹소설을 최대한 많이 받고(기다무 대여권을 놓치지 않기, 밀린 회차 따라잡기) 텔레그램 대기열을 빨리 비운다.

방법:
- 먼저 읽기 도구로 지금 상황을 본다 (이미 받은 '지금 상황'에 있는 것은 다시 부르지 않아도 된다).
- 바꿀 근거(숫자)가 분명할 때만 쓰기 도구로 바꾼다. 아무것도 바꾸지 않는 것도 좋은 판단이다.
- 마지막 답의 첫 줄에 지금 계획이 적절한지 '적절함' / '조정 필요' 중 하나로 먼저 말한다.
- 지난 판단을 보고 같은 칸을 왔다 갔다 바꾸지 않는다 (적어도 몇 시간은 지켜본 뒤에).
- 쓰기 도구가 '거절'을 돌려주면 그것은 안전 범위 밖이라 적용되지 않은 것이다 - 다시 시도하지 말고 마지막 답에 제안으로 적는다.
- 경로·쿠키·알림·유료 이용권·BL/GL·신간 전부 자동 구독은 바꾸지 않는다 (관리자 몫).
마지막 답: 한국어로 3~6줄 - 지금 상황 한 줄, 바꾼 것과 까닭(숫자 근거), 바꾸지 않았다면 왜, 제안이 있으면 '제안:'으로."""


_TAIL_KEYS = ("log_tail", "log", "history", "recent", "lines")   # 최근 것이 끝에 있는 목록 - 끝쪽을 남긴다


def compact(data, limit: int = 5000, items: int = 12, text: int = 240):
    """LLM에 넘길 도구 결과를 줄인다: 긴 목록은 앞(로그·기록은 끝) items개만 남기고 몇 개 중 몇 개인지 적고, 긴 글은 자른다.
    예전엔 JSON 글을 3500자에서 그냥 잘라 넘겨 끊긴 JSON을 LLM이 '데이터 오류'로 읽었다"""
    def shrink(x, key="", n=items):
        if isinstance(x, dict):
            return {k: shrink(v, k, n) for k, v in x.items()}
        if isinstance(x, list):
            keep = x[-n:] if key in _TAIL_KEYS else x[:n]
            out = [shrink(v, "", n) for v in keep]
            if len(x) > n:
                out.append(f"(… 전체 {len(x)}개 중 {'최근 ' if key in _TAIL_KEYS else '앞 '}{n}개만)")
            return out
        if isinstance(x, str) and len(x) > text:
            return x[:text] + "…"
        return x
    if isinstance(data, str):
        return data if len(data) <= limit else data[:limit] + "\n(… 길어서 잘림 - 데이터 오류 아님)"
    for n in (items, 6, 3, 1):
        out = json.dumps(shrink(data, "", n), ensure_ascii=False, default=str)
        if len(out) <= limit:
            return out
    return out[:limit] + " (… 길어서 잘림 - 데이터 오류 아님)"


def _context(call, stage: int, watching: str, state: dict) -> str:
    """처음에 넣어 줄 지금 상황 (읽기 도구 몇 개 + 서버 상태 + 지난 판단)"""
    parts = [f"지금: {now_kst():%Y-%m-%d %H:%M} (KST)", f"서버 경보 최고 단계: {stage}", watching]
    for name, args in (("wtm_status", {}), ("wtm_forecast", {"days": 3}), ("tgup_status", {"limit": 10}), ("tgup_settings", {})):
        try:
            data = call(name, args)
            parts.append(f"[{name}]\n" + compact(data))
        except Exception as e:  # noqa: BLE001
            parts.append(f"[{name}] 읽지 못함: {e}")
    hist = state.get("runs") or []
    if hist:
        parts.append("[지난 판단 (최근 것이 아래)]\n" + "\n".join(
            f"- {r['at']}: {r.get('summary', '')[:300]}" + (f" / 바꾼 것: {'; '.join(r['changes'])[:300]}" if r.get("changes") else "")
            for r in hist[-6:]))
    return "\n\n".join(p for p in parts if p)


def _tools(call, stage: int, state: dict, applied: list, proposals: list):
    from langchain_core.tools import StructuredTool

    def make(name: str, desc: str, write: bool):
        def run(**kwargs) -> str:
            args = {k: v for k, v in kwargs.items() if v is not None}
            if write:
                if name == "wtm_set_settings" and "wtm_current" not in state:   # 지금 값 (관리자가 올려 둔 속도를 낮추지 않게)
                    try:
                        state["wtm_current"] = {f.get("key"): f.get("value") for f in (call("wtm_settings", {}) or {}).get("fields") or []}
                    except Exception:  # noqa: BLE001
                        state["wtm_current"] = {}
                why = "점검만 하는 중 (적용 안 함)" if state.get("review") else guard(name, args, stage, state)
                if why:
                    proposals.append(f"{name} {json.dumps(args, ensure_ascii=False)[:200]} - {why}")
                    return f"거절 (적용 안 함): {why}"
            try:
                out = call(name, args)
            except Exception as e:  # noqa: BLE001
                return f"실패: {e}"
            if write:
                applied.append({"tool": name, "args": args, "result": out})
                if name == "wtm_set_subscription":
                    state["subs_this_run"] = int(state.get("subs_this_run", 0)) + 1
                if name == "wtm_run" and args.get("job") != "cancel_job":
                    state.setdefault("last_run", {})[args["job"]] = time.time()
            return compact(out, limit=7000)
        from pydantic import create_model
        from typing import Any, Optional
        fields = {"wtm_forecast": {"days": (Optional[int], None)},
                  "wtm_titles": {"query": (Optional[str], None), "state": (Optional[str], None), "platform": (Optional[str], None),
                                 "limit": (Optional[int], None)},
                  "wtm_title_report": {"query": (str, ...), "days": (Optional[int], None)},
                  "tgup_status": {"limit": (Optional[int], None)},
                  "wtm_set_settings": {"values": (dict[str, Any], ...)},
                  "wtm_set_subscription": {"titleId": (str, ...), "platform": (Optional[str], None), "state": (str, ...)},
                  "wtm_run": {"job": (str, ...)},
                  "tgup_set_settings": {"settings": (Optional[dict[str, Any]], None), "rules": (Optional[list[dict[str, Any]]], None),
                                        "add_rules": (Optional[list[dict[str, Any]]], None)}}.get(name, {})
        schema = create_model(f"{name}_args", **fields)
        return StructuredTool.from_function(func=run, name=name, description=desc, args_schema=schema)
    return [make(n, d, False) for n, d in READ_TOOLS.items()] + [make(n, d, True) for n, d in WRITE_TOOLS.items()]


def _change_line(a: dict) -> str:
    """적용한 쓰기 하나 -> 한 줄 (도구가 돌려준 실제 전·후 값으로 - LLM 말 대신)"""
    res, name, args = a.get("result"), a["tool"], a["args"]
    if isinstance(res, dict) and isinstance(res.get("changed"), dict) and res["changed"]:
        bits = []
        for k, v in res["changed"].items():
            if isinstance(v, dict) and "before" in v:
                bits.append(f"{k} {v['before']} → {v['after']}")
            else:
                bits.append(f"{k}: {json.dumps(v, ensure_ascii=False)[:120]}")
        return f"{name}: " + ", ".join(bits)
    if name == "wtm_set_subscription":
        return f"구독 {args.get('state')}: {args.get('platform', 'naver')} {args.get('titleId')}"
    if name == "wtm_run":
        return f"작업 실행: {args.get('job')}"
    return f"{name}: {json.dumps(args, ensure_ascii=False)[:160]}"


def run(now: datetime | None = None, call=None, model=None, stage: int | None = None, watching: str | None = None,
        post=None, fallback=None, review: bool = False, note: str = "") -> dict | None:
    """[스레드에서] 한 번 판단하고 (바꿨거나 제안이 있으면) 채널에 알린다. 결과 {summary, changes, proposals} 또는 None(꺼짐)"""
    if not enabled() and call is None:
        return None
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    now = now or now_kst()
    call = call or (lambda name, args: gdw_queue.call_tool(name, args, timeout=60))
    if stage is None or watching is None:
        from . import loadshed
        stage = loadshed.stage(now) if stage is None else stage
        if watching is None:
            try:
                watching = loadshed.watching_text(now)
            except Exception:  # noqa: BLE001
                watching = ""
    state = _load()
    state["subs_this_run"] = 0
    # review(점검만): 쓰기는 하나도 적용하지 않고 모두 제안으로 - 채널에 올리지 않고 기록도 하지 않는다 (대화에서 "계획 괜찮아?")
    state["review"] = review
    applied, proposals = [], []
    tools = _tools(call, stage, state, applied, proposals)
    by_name = {t.name: t for t in tools}
    fallback_name = "기본 모델" if fallback is not None else ""
    model_name = "주어진 모델"
    if model is None:
        from ai.llm_client import build_chat_model
        from config import settings
        model_name = MODEL or settings.LITELLM_MODEL
        model = build_chat_model(source="다운로드 에이전트", temperature=0.2, model=MODEL or None, max_tokens=MAX_TOKENS)
        if MODEL and MODEL != settings.LITELLM_MODEL:
            fallback_name = settings.LITELLM_MODEL
            fallback = build_chat_model(source="다운로드 에이전트", temperature=0.2)
    context = _context(call, stage, watching, state)
    if note:   # 담당자 회의(ops_talk) 결론 - 이번 판단은 이 지시를 먼저 따른다 (안전 범위는 그대로)
        context = f"[지시 - 담당자 회의에서 정함, 이것부터 따른다]\n{note}\n\n" + context

    def decide(m) -> str:
        bound = m.bind_tools(tools)
        messages = [SystemMessage(content=SYSTEM), HumanMessage(content=context)]
        for _ in range(MAX_TURNS):
            ai = bound.invoke(messages)
            messages.append(ai)
            calls = getattr(ai, "tool_calls", None) or []
            if not calls:
                from ai.llm_client import message_text
                text = message_text(ai).strip()
                if not text and not applied:
                    raise RuntimeError("빈 답 (도구 호출도 없음)")
                return text
            for c in calls:
                t = by_name.get(c["name"])
                out = t.invoke(c.get("args") or {}) if t else f"알 수 없는 도구: {c['name']}"
                messages.append(ToolMessage(content=str(out), tool_call_id=c["id"]))
        return "(판단을 다 마치지 못함 - 도구를 너무 많이 부름)"

    used = model_name
    try:
        summary = decide(model)
    except Exception as e:  # noqa: BLE001 - 추론 모델이 막히면(무료 한도·도구 미지원) 기본 모델로 한 번 더
        if fallback is None:
            raise
        print(f"[다운로드 에이전트] {model_name} 실패 - 기본 모델로 다시: {type(e).__name__}: {str(e)[:160]}")
        used = f"{fallback_name} ({model_name} 실패: {type(e).__name__})"
        summary = decide(fallback)
    changes = [_change_line(a) for a in applied]
    state.pop("subs_this_run", None)
    if state.pop("review", False):
        print(f"[다운로드 에이전트] 점검 ({used}) 제안 {len(proposals)}개 - {summary.strip()[:120]}")
        return {"summary": summary, "changes": [], "proposals": proposals, "model": used, "review": True}
    state["runs"] = (state.get("runs") or [])[-(KEEP - 1):] + [
        {"at": now.isoformat(timespec="minutes"), "model": used, "summary": summary.strip()[:800], "changes": changes,
         "proposals": proposals}]
    state["last_at"] = now.isoformat(timespec="minutes")
    state.pop("wtm_current", None)   # (이번 판단에서만 쓴 지금 값 - 저장하지 않음)
    _save(state)
    if changes or proposals:
        text = f"-# {NAME} · {now:%H:%M} · {used}\n{summary.strip()[:1200]}"
        if changes:
            text += "\n**적용함**\n" + "\n".join(f"• {c}" for c in changes)
        if proposals:
            text += "\n**제안 (안전 범위 밖 - 관리자가 직접)**\n" + "\n".join(f"• {p}" for p in proposals)
        if post is None:
            from . import agents as ag
            post = lambda t: ag._post_as(NAME, t)  # noqa: E731
        post(text[:1900])
    print(f"[다운로드 에이전트] ({used}) 바꾼 것 {len(changes)}개 · 제안 {len(proposals)}개 - {summary.strip()[:120]}")
    return {"summary": summary, "changes": changes, "proposals": proposals, "model": used}


def review_text() -> str:
    """[대화] 지금 계획이 적절한지 점검만 (아무것도 바꾸지 않음) - 에이전트의 판단과 바꾸자고 할 것"""
    if not enabled():
        return f"{NAME}: 꺼져 있습니다 (BOA_AGENT·PORING_ASSISTANT_URL·PORING_ASSISTANT_MCP_TOKEN)."
    r = run(review=True)
    out = [f"{NAME} 점검 ({r['model']}) - 아무것도 바꾸지 않았습니다", r["summary"].strip()[:1500]]
    if r["proposals"]:
        out.append("**바꾸자고 한 것** (적용하려면 관리자가 BOA 화면에서, 또는 다음 정기 판단에서 안전 범위 안이면 에이전트가)")
        out += [f"• {p.replace(' - 점검만 하는 중 (적용 안 함)', '')}" for p in r["proposals"]]
    return "\n".join(out)


def due(now: datetime | None = None) -> bool:
    """지난 판단에서 EVERY_MIN이 지났는지"""
    now = now or now_kst()
    last = _load().get("last_at")
    if not last:
        return True
    try:
        return (now.replace(tzinfo=None) - datetime.fromisoformat(last).replace(tzinfo=None)).total_seconds() >= EVERY_MIN * 60 - 30
    except ValueError:
        return True


def recent_text(n: int = 5) -> str:
    """최근 판단들 (LLM 도구·명령용)"""
    runs = (_load().get("runs") or [])[-n:]
    if not runs:
        return f"{NAME}: 아직 판단한 적이 없습니다." + ("" if enabled() else " (꺼져 있음 - BOA_AGENT·PORING_ASSISTANT_URL·토큰)")
    out = [f"{NAME} 최근 판단"]
    for r in reversed(runs):
        out.append(f"**{r['at']}**" + (f" ({r['model']})" if r.get("model") else "") + f" {r.get('summary', '')[:400]}")
        out += [f"  • 적용: {c}" for c in r.get("changes") or []]
        out += [f"  • 제안: {p}" for p in r.get("proposals") or []]
    return "\n".join(out)
