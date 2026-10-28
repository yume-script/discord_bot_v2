"""기다무 충전 시간대 - 짐꾼(plex01 bookoasis_assistant)의 웹툰/웹소설 대여권이 서버가 한가한 시간대에 충전·사용되게.

대여권은 '마지막으로 쓴 시각 + 주기'마다 충전되고 충전되면 바로 받으러 가므로, 충전 시각이 곧 다운로드 시각이다.
bookoasis_assistant의 `python3 -m wtm.shift_waitfree --spread 시작 끝 [--apply]`가 실제로 대여권을 쓰는 1·2·3일 기다무를
그 시간대에 고르게 다시 놓는다(앞당기지는 않음 - 카카오가 거절). 이 모듈은
  1) 결계 조절(loadshed)이 매분 남기는 시간대별 기록(부하 지수·재생 여부, 최근 14일)으로 가장 한가한 연속 시간대를 고르고
     (영상 재생이 있었던 시간은 크게 감점 - 재생이 1순위, 새벽 FF rclone 새로 고침처럼 늘 바쁜 시간도 지수로 드러난다),
  2) 원격으로 미리 보기를 돌려 분포를 보여 주고,
  3) 적용은 안전할 때만: 지금 재생 없음 · plex01 결계 2단계 이하 · 지난 적용 뒤 20시간 이상 · 시간대 4~16시간.
     작업 프로세스가 작품 기록을 덮어쓰지 않게 서비스를 멈췄다가 적용하고 반드시 다시 켠다(받던 회차는 다음에 이어 받음).
LLM 도구: get_download_plan(조회) / preview_waitfree_window(조회) / set_waitfree_window(관리자 + 확인 뒤 실행).
출력에서 작품 제목 예시 줄은 지운다(금서고 제목이 섞일 수 있음) - 시간대별 개수와 다운로드 통계(숫자)만.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from datetime import datetime, timedelta

from . import loadshed
from ._log import pf_print as print  # print()를 봇 로그로
from .clock import now_kst
from .config import STATE_DIR

HOST = "plex01"
DIR = os.getenv("PORING_BOOKOASIS_ASSISTANT_DIR", "/opt/bookoasis_assistant")
UNIT = os.getenv("PORING_BOOKOASIS_ASSISTANT_UNIT", "bookoasis_assistant.service")
STATE_PATH = os.path.join(STATE_DIR, "waitfree.json")
DEFAULT = (1, 11)             # 기록이 모자랄 때 (bookoasis_assistant 기본값)
MIN_DAYS = 3                  # 시간대마다 이만큼의 날 기록이 있어야 추천
MIN_HOURS, MAX_HOURS = 4, 16  # 시간대 길이 (너무 좁으면 한꺼번에 몰려 부하, 너무 넓으면 바쁜 시간까지)
WATCH_PENALTY = 40            # 그 시간에 늘 재생 중이었으면(비율 1.0) 지수 +40%p로 친다
APPLY_GAP_H = 20              # 적용은 하루 한 번쯤 (옮길 때마다 대여권 한 장이 최대 하루 늦어진다)
MAX_STAGE = 2


# ===================================================================== 시간대별 기록
def profile(data: dict | None = None) -> dict[int, dict]:
    """{시(0~23): {"idx": 평균 지수, "watch": 재생 비율 0~1 또는 None, "days": 기록된 날 수}}"""
    hourly = (data if data is not None else loadshed._load()).get("hourly") or {}
    acc: dict[int, list] = {}
    for key, (tot, n, known, play) in hourly.items():
        if not n:
            continue
        a = acc.setdefault(int(key[-2:]), [0.0, 0, 0.0, 0])
        a[0] += tot / n
        a[1] += 1
        if known:
            a[2] += play / known
            a[3] += 1
    return {h: {"idx": round(a[0] / a[1], 1), "watch": round(a[2] / a[3], 2) if a[3] else None, "days": a[1]}
            for h, a in sorted(acc.items())}


def _score(p: dict) -> float:
    return p["idx"] + WATCH_PENALTY * (p["watch"] or 0)


def best_window(hours: int = 10, prof: dict | None = None) -> tuple[int, int, str]:
    """가장 한가한 연속 hours시간 (시작, 끝(미포함), 이유). 기록이 모자라면 기본값."""
    prof = profile() if prof is None else prof
    hours = max(MIN_HOURS, min(MAX_HOURS, int(hours)))
    if len(prof) < 24 or min(p["days"] for p in prof.values()) < MIN_DAYS:
        have = min((p["days"] for p in prof.values()), default=0) if len(prof) == 24 else 0
        return DEFAULT[0], DEFAULT[1], f"기록이 모자라 기본값 (시간대마다 {MIN_DAYS}일 필요, 지금 {have}일)"
    best = None
    for s in range(24):
        span = [prof[(s + i) % 24] for i in range(hours)]
        key = (sum(_score(p) for p in span) / hours, max(_score(p) for p in span))
        if best is None or key < best[0]:
            best = (key, s)
    (avg, worst), s = best
    return s, (s + hours) % 24, f"그 시간대 평균 점수 {avg:.0f} (가장 바쁜 시 {worst:.0f}) - 점수 = 부하 지수 + 재생 비율×{WATCH_PENALTY}"


# ===================================================================== 원격 실행 (plex01)
def _remote(script: str, timeout: int) -> tuple[int, str]:
    cmd = loadshed._cmd(HOST, [script]) if loadshed._ssh_of(HOST) else ["sh", "-c", script]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, f"plex01 명령 실패: {e!r}"[:200]
    return p.returncode, (p.stdout + (("\n" + p.stderr.strip()[-300:]) if p.returncode and p.stderr.strip() else ""))


def _py(args: str) -> str:
    return (f"cd {shlex.quote(DIR)} && P=python3 && {{ [ -x venv/bin/python ] && P=venv/bin/python; true; }} && "
            f"$P -m {args}")


def _clean(out: str) -> str:
    """작품 제목이 든 '예:' 줄들을 뺀다 (숫자만 남김)."""
    keep, skip = [], False
    for line in out.splitlines():
        if line.strip() == "예:":
            skip = True
            continue
        if skip and (not line.startswith("  ") or not line.strip()):
            skip = False
        if not skip:
            keep.append(line)
    return "\n".join(keep).strip()


def _window_ok(start: int, end: int) -> str | None:
    if not (0 <= int(start) <= 23 and 0 <= int(end) <= 23):
        return "시각은 0~23시로"
    n = (int(end) - int(start)) % 24
    if not MIN_HOURS <= n <= MAX_HOURS:
        return f"시간대 길이는 {MIN_HOURS}~{MAX_HOURS}시간 (지금 {n}시간)"
    return None


# ===================================================================== 기록
def _state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def guard(now: datetime | None = None, playing: int | None = -1) -> str | None:
    """지금 적용하면 안 되는 이유 (괜찮으면 None). playing=-1이면 직접 잰다."""
    now = now or now_kst()
    last = (_state().get("applied") or [{}])[-1].get("at", "")
    if last and last > (now - timedelta(hours=APPLY_GAP_H)).isoformat():
        return f"마지막 적용({last[5:16].replace('T', ' ')}) 뒤 {APPLY_GAP_H}시간이 안 지났어요"
    if playing == -1:
        playing = loadshed._viewing()[0]
    if playing:
        return f"지금 영상 {playing}개 재생 중 - 짐꾼을 잠깐 멈춰야 해서 재생이 없을 때만 해요"
    st = (loadshed._load().get("hosts") or {}).get(HOST) or {}
    if int(st.get("stage", 0)) > MAX_STAGE:
        return f"plex01 결계 {st.get('stage')}단계 - {MAX_STAGE}단계 이하일 때만 해요"
    return None


# ===================================================================== 도구가 부르는 것
def plan_text(now: datetime | None = None) -> str:
    now = now or now_kst()
    prof = profile()
    s, e, why = best_window(10, prof)
    lines = [f"■ 기다무 충전 시간대 추천: {s:02d}:00~{(e - 1) % 24:02d}:59 ({why})"]
    if prof:
        lines.append("  시간대별 (최근 14일 평균) 시: 부하 지수 / 재생 비율")
        lines += ["  " + " · ".join(f"{h:02d}시 {p['idx']:.0f}%/" + ("?" if p["watch"] is None else f"{p['watch'] * 100:.0f}%")
                                     for h, p in list(prof.items())[i:i + 6]) for i in range(0, len(prof), 6)]
    last = (_state().get("applied") or [])[-1:]
    if last:
        a = last[0]
        lines.append(f"  마지막 적용: {a['at'][5:16].replace('T', ' ')} {a['start']:02d}~{a['end']:02d}시"
                     + (f" ({a['by']})" if a.get("by") else ""))
    why_not = guard(now)
    lines.append("  지금 적용: " + ("가능" if not why_not else f"안 됨 - {why_not}"))
    rc, out = _remote(_py("wtm.download_stats 7"), 60)
    lines.append("\n" + (_clean(out) if rc == 0 else f"다운로드 통계를 못 읽음: {out.strip()[:200]}"))
    return "\n".join(lines)


def preview(start: int, end: int) -> str:
    bad = _window_ok(start, end)
    if bad:
        return f"시간대가 이상해요: {bad}"
    rc, out = _remote(_py(f"wtm.shift_waitfree --spread {int(start)} {int(end)}"), 120)
    return _clean(out) if rc == 0 else f"미리 보기 실패: {out.strip()[:300]}"


def apply(start: int, end: int, by: str = "", now: datetime | None = None) -> str:
    now = now or now_kst()
    bad = _window_ok(start, end)
    if bad:
        return f"시간대가 이상해요: {bad}"
    why_not = guard(now)
    if why_not:
        return f"지금은 적용하지 않았어요: {why_not}"
    if loadshed.DRY_RUN:
        return "시험 모드(PORING_LOADSHED_DRY=1)라 미리 보기만:\n" + preview(start, end)
    u = shlex.quote(UNIT)
    script = (f"systemctl stop {u}; {_py(f'wtm.shift_waitfree --spread {int(start)} {int(end)} --apply')}; rc=$?; "
              f"systemctl start {u}; systemctl is-active {u}; exit $rc")
    rc, out = _remote(script, 240)
    if rc != 0:
        return f"적용 실패 (짐꾼은 다시 켬): {_clean(out)[-400:]}"
    st = _state()
    st["applied"] = (st.get("applied") or [])[-19:] + [{"at": now.isoformat(timespec="minutes"), "start": int(start),
                                                       "end": int(end), "by": by}]
    _save_state(st)
    print(f"[기다무 시간대] {start:02d}~{end:02d}시 적용 ({by or '도구'})")
    return _clean(out)


# ===================================================================== 자율 (짐꾼 담당 인물이 스스로 판단)
AUTO = os.getenv("PORING_WAITFREE_AUTO", "1") not in ("0", "false", "False", "")
CHECK_GAP_H = 20          # 하루 한 번쯤 살펴본다 (적용 간격 APPLY_GAP_H와 같은 이유)
RETRY_MIN = 30            # 재생 중/단계 높음으로 못 했으면 이만큼 뒤에 다시 (그날 안에)

DECIDE_SYSTEM = """너는 오아시스 마을 도서관의 짐꾼(웹툰/웹소설 다운로더 bookoasis_assistant) 담당자다.
목표 순서: 1) 영상 재생·책 읽기에 전혀 불편이 없게 2) 서버 부하 지수 95% 이하 3) 그 안에서 최대한 많이 받기.
기다무 대여권은 충전되는 순간 받으러 가므로 '충전 시간대'가 곧 다운로드 시간대다. 시간대를 옮기면 작품마다 대여권
한 장이 최대 하루 늦어지므로, 지금 시간대보다 확실히 나을 때만 바꾼다(재생이 많은 시간이나 늘 바쁜 시간과 겹칠 때,
아직 한 번도 맞춘 적이 없을 때). 최근 다운로드 수가 지난 변경 뒤 크게 줄었으면 그것도 근거로 본다.
시간대는 0~23시, 끝은 미포함, 길이 4~16시간. 추천 시간대를 그대로 쓰는 것이 기본이다.
JSON으로만 답한다: {"apply": true/false, "start": 시작시, "end": 끝시, "reason": "한두 문장, 숫자 근거"}"""


def _decider() -> str:
    try:
        from . import worldcfg
        from .agents import load_agents
        company = getattr(worldcfg, "BOOK", "")
        return next((a["name"] for a in load_agents() if a.get("company") == company), loadshed.ACTOR)
    except Exception:  # noqa: BLE001
        return loadshed.ACTOR


def autopilot(now: datetime | None = None, decide=None, post=None, playing: int | None = -1) -> dict | None:
    """[30분마다, 스레드에서] 하루 한 번: 근거(시간대별 부하·재생, 추천, 지난 적용, 최근 다운로드 수, 미리 보기)를 모아
    담당 인물(LLM)이 바꿀지 정하고, 정하면 안전장치(guard)를 통과할 때만 적용한다. 채널에 판단과 결과를 알린다.
    반환: 이번에 한 판단 (안 살펴봤으면 None)."""
    if not AUTO:
        return None
    now = now or now_kst()
    st = _state()
    last = st.get("checked_at", "")
    if last and last > (now - timedelta(hours=CHECK_GAP_H)).isoformat():
        return None
    if st.get("retry_at", "") > now.isoformat():
        return None
    prof = profile()
    s, e, why = best_window(10, prof)
    if "기본값" in why:                       # 기록이 모자라면 스스로 바꾸지 않는다 (LLM도 부르지 않음)
        return None
    applied = (st.get("applied") or [])[-1:]
    cur = (applied[0]["start"], applied[0]["end"]) if applied else None
    if cur == (s, e):
        st["checked_at"] = now.isoformat(timespec="minutes")
        _save_state(st)
        return None
    why_not = guard(now, playing)
    if why_not:                               # 안전하지 않으면 LLM도 부르지 않고 잠시 뒤 다시
        st["retry_at"] = (now + timedelta(minutes=RETRY_MIN)).isoformat(timespec="minutes")
        _save_state(st)
        return None
    who = _decider()
    facts = (plan_text(now) + "\n\n■ 추천 시간대 미리 보기\n" + preview(s, e)
             + "\n\n지금 맞춰 둔 시간대: " + (f"{cur[0]:02d}~{cur[1]:02d}시 ({applied[0]['at'][:16]})" if cur else "없음 (아직 맞춘 적 없음)"))
    if decide is None:
        from .llm import llm_json
        decide = lambda: llm_json(DECIDE_SYSTEM, facts, temperature=0.2, timeout=60, tag="기다무 시간대", agent=who)  # noqa: E731
    d = decide() or {}
    st = _state()
    st["checked_at"] = now.isoformat(timespec="minutes")
    st.pop("retry_at", None)
    try:
        start, end = int(d.get("start", s)), int(d.get("end", e))
    except (TypeError, ValueError):
        start, end = s, e
    reason = str(d.get("reason") or "").strip()[:300]
    result = ""
    if d.get("apply") and not _window_ok(start, end):
        _save_state(st)
        result = apply(start, end, by=f"자율({who})", now=now)
        st = _state()
    elif d.get("apply"):
        result = f"시간대가 이상해서 하지 않음: {_window_ok(start, end)}"
    st["decisions"] = (st.get("decisions") or [])[-19:] + [{"at": now.isoformat(timespec="minutes"), "who": who,
                                                           "apply": bool(d.get("apply")), "start": start, "end": end,
                                                           "reason": reason, "result": result[:200]}]
    _save_state(st)
    done = {"who": who, "apply": bool(d.get("apply")), "start": start, "end": end, "reason": reason, "result": result}
    head = (f"기다무 충전 시간대를 {start:02d}:00~{(end - 1) % 24:02d}:59로 옮기기로 했어요" if done["apply"]
            else f"기다무 충전 시간대는 그대로 두기로 했어요 (추천 {s:02d}~{e:02d}시)")
    tail = ""
    if done["apply"]:
        tail = "\n→ " + ("적용함 (짐꾼 잠깐 멈췄다 다시 켬)" if "적용했습니다" in result else result.splitlines()[-1][:200] if result else "결과 없음")
    text = f"-# {loadshed.NAME} · {now:%H:%M} · plex01 `bookoasis_assistant` 기다무 시간대\n**{who}**: {head} - {reason or '이유 없음'}{tail}"
    try:
        if post is None:
            from .agents import _post_as
            post = lambda t: _post_as(loadshed.NAME, t)  # noqa: E731
        post(text)
        from . import memory
        memory.remember(who, f"{head} ({reason})", 4, [], "짐꾼")
    except Exception as ex:  # noqa: BLE001
        print(f"[기다무 시간대] 알림/기억 실패: {ex}")
    print(f"[기다무 시간대] {who}: apply={done['apply']} {start}~{end} - {reason[:80]}")
    return done
