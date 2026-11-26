"""
채우기 운영 루프 - 아무도 안 볼 때 서버를 놀리지 않고 사용자 데이터(웹툰·영상)를 최대한 채운다. LLM 없이 규칙으로, 5분마다.

목표 순서는 서버 조절(loadshed)과 같다: 1) 재생·열람이 끊기지 않게 2) 그 안에서 자료를 최대한 많이.
서버 조절은 '바쁠 때 늦추기'만 하고 되돌리기는 시간이 지나야 했다 - 여기서는 반대로 '한가할 때 다 돌리기'를 맡는다.

모드 (서버 조절이 매분 남긴 plex01의 재생 수·경보 단계로 정한다, 숫자를 못 읽으면 아무것도 안 바꿈):
  yield  재생 중 (Plex·YM_PLAYER·북오아시스 열람)        -> 텔레그램 인코딩 우선순위 '낮게' (재생 변환이 먼저)
  fill   재생이 끝나고 IDLE_MIN(10분) 지남 + 경보 MAX_STAGE(2)단계 이하
         -> 텔레그램 인코딩 우선순위 '보통'(가장 빠름)
         -> rclone 업로드 속도 제한 풀기 (서버 조절이 늦추고 있지 않을 때만, PORING_LOADSHED_RCLONE_RC가 있을 때)
         -> 웹툰 다운로더가 놀고 있으면 전체 실행 (RUN_GAP_MIN(180분)에 한 번까지 - 사이트에 몰아치지 않게,
            카카오 기다무가 밀려 있으면(BOA 작업 상태 waitfree_backlog) 하지 않음 - 같은 계정·서버를 나눠 써서 기다무가 더 늦어짐)
  hold   그 사이 (재생 끝난 지 얼마 안 됨, 경보가 높음, 숫자 모름) -> 그대로 둔다
- 바뀔 때만 쓰고(지금 값과 같으면 안 씀), 한 일만 채널(⚙️ 운영)에 한 줄로 알린다. 인물 대화·LLM 없음.
- 웹툰 다운로더의 동시 수·일정, 텔레그램 규칙·시간대는 건드리지 않는다 (사람 또는 다운로드 에이전트가 정함).
설정 (.env): PORING_FILL=0이면 끔, PORING_FILL_MAX_STAGE, PORING_FILL_IDLE_MIN, PORING_FILL_RUN_GAP_MIN, PORING_FILL_WTM_RUN=0(웹툰 실행 안 함)
저장: storage/poring_food/fill.json {"mode", "since", "last_play", "last_run", "last"}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로
from .config import STATE_DIR

ENABLED = os.getenv("PORING_FILL", "1") not in ("0", "false", "False", "")
MAX_STAGE = int(os.getenv("PORING_FILL_MAX_STAGE", "2"))
IDLE_MIN = int(os.getenv("PORING_FILL_IDLE_MIN", "10"))
RUN_GAP_MIN = int(os.getenv("PORING_FILL_RUN_GAP_MIN", "180"))
WTM_RUN = os.getenv("PORING_FILL_WTM_RUN", "1") not in ("0", "false", "False", "")
FRESH_MIN = 5                      # 서버 조절 기록이 이보다 오래됐으면 모르는 것으로
HOST = "plex01"
STATE_PATH = os.path.join(STATE_DIR, "fill.json")
NAME = "⚙️ 운영"
PRIORITY = {"fill": "high", "yield": "low"}
PRIORITY_LABEL = {"high": "보통(가장 빠름)", "normal": "조금 낮게", "low": "낮게"}
MODE_LABEL = {"fill": "채우기 (아무도 안 봄)", "yield": "양보 (재생 중)", "hold": "그대로"}


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(STATE_PATH + ".tmp", STATE_PATH)


def observe(now: datetime) -> dict:
    """서버 조절이 매분 남긴 plex01 기록 -> {"playing": int|None, "stage": int|None, "rclone_slowed": bool}"""
    from . import loadshed
    st = ((loadshed._load().get("hosts") or {}).get(HOST)) or {}
    at = st.get("at")
    fresh = False
    if at:
        try:
            fresh = now - datetime.fromisoformat(at) <= timedelta(minutes=FRESH_MIN)
        except ValueError:
            fresh = False
    if not fresh:
        return {"playing": None, "stage": None, "rclone_slowed": False}
    playing = st.get("playing")
    return {"playing": None if playing is None else int(playing), "stage": int(st.get("stage", 0)),
            "rclone_slowed": "rclone:upload" in (st.get("slow") or {})}


def decide(obs: dict, state: dict, now: datetime) -> str:
    """fill / yield / hold"""
    if obs.get("playing") is None or obs.get("stage") is None:
        return "hold"
    if obs["playing"] > 0:
        return "yield"
    last = state.get("last_play")
    if last and now - datetime.fromisoformat(last) < timedelta(minutes=IDLE_MIN):
        return "hold"
    if obs["stage"] > MAX_STAGE:
        return "hold"
    return "fill"


# ── 바꾸는 곳 (BOA MCP · rclone rc) - 시험에서 바꿔 끼운다 ──
def _boa(name: str, args: dict | None = None):
    from . import gdw_queue
    return gdw_queue.call_tool(name, args or {}, timeout=20)


def _rclone_rate(rate: str | None = None) -> str | None:
    """rclone rc core/bwlimit - rate를 주면 바꾸고, 안 주면 지금 값. 주소가 없거나 실패하면 None"""
    import requests

    from . import loadshed
    url = loadshed.HOSTS[HOST].get("rclone")
    if not url:
        return None
    try:
        r = requests.post(f"{url}/core/bwlimit", json={"rate": rate} if rate else {}, timeout=8)
        return str((r.json() or {}).get("rate") or "") if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001
        print(f"[운영] rclone bwlimit 실패: {e!r}"[:160])
        return None


def apply(mode: str, obs: dict, state: dict, now: datetime, boa=None, rclone=None) -> list[str]:
    """모드에 맞게 맞춘다 -> 한 일 (한 줄씩). 실패는 로그만 (다음 5분에 다시)"""
    boa, rclone = boa or _boa, rclone or _rclone_rate
    done = []
    want = PRIORITY.get(mode)
    if want:
        try:
            cur = (boa("tgup_settings") or {}).get("encode_priority") or "low"
            if cur != want:
                boa("tgup_set_settings", {"settings": {"encode_priority": want}})
                done.append(f"텔레그램 인코딩 우선순위 {PRIORITY_LABEL.get(cur, cur)} → {PRIORITY_LABEL[want]}")
        except Exception as e:  # noqa: BLE001
            print(f"[운영] 텔레그램 인코딩 우선순위를 맞추지 못함: {e}")
    if mode != "fill":
        return done
    if not obs.get("rclone_slowed"):   # 서버 조절이 늦추는 중이면 그쪽이 되돌린다
        cur = rclone()
        if cur and cur.lower() not in ("off", "0", ""):
            if rclone("off") is not None:
                done.append(f"rclone 업로드 속도 제한 풀기 ({cur} → 제한 없음)")
    if WTM_RUN:
        last = state.get("last_run")
        if not last or now - datetime.fromisoformat(last) >= timedelta(minutes=RUN_GAP_MIN):
            try:
                st = boa("wtm_status") or {}
                job = st.get("job") or {}
                busy = bool(job.get("running")) or bool((st.get("title_job") or {}).get("running"))
                # 카카오 기다무가 밀려 있으면 전체 실행을 따로 돌리지 않는다 (같은 계정·서버를 나눠 써서 기다무가 더 늦어짐) - BOA 2.80.0+
                backlog = bool(job.get("waitfree_backlog")) and (now.timestamp() - float(job.get("waitfree_due_at") or 0)) < 3600
                if backlog:
                    state["skipped_backlog"] = now.isoformat(timespec="seconds")
                elif not busy:
                    boa("wtm_run", {"job": "run_full_cycle_now"})
                    state["last_run"] = now.isoformat(timespec="seconds")
                    done.append("웹툰 다운로더가 놀고 있어 전체 실행 시작")
            except Exception as e:  # noqa: BLE001
                print(f"[운영] 웹툰 다운로더 확인·실행 실패: {e}")
    return done


def step(now: datetime | None = None, obs: dict | None = None, boa=None, rclone=None) -> list[str]:
    """[스레드에서, 5분마다] 알릴 글들 (없으면 [])"""
    if not ENABLED:
        return []
    from . import gdw_queue
    if boa is None and not gdw_queue.enabled():
        return []
    now = now or now_kst()
    obs = obs if obs is not None else observe(now)
    state = _load()
    stamp = now.isoformat(timespec="seconds")
    if obs.get("playing"):
        state["last_play"] = stamp
    mode = decide(obs, state, now)
    msgs = []
    if mode != state.get("mode"):
        if mode != "hold":
            msgs.append(f"모드: **{MODE_LABEL[mode]}** (재생 {obs.get('playing')} · 서버 {obs.get('stage')}단계)")
        state["mode"], state["since"] = mode, stamp
    done = apply(mode, obs, state, now, boa, rclone) if mode != "hold" else []
    msgs += [f"• {d}" for d in done]
    if done:
        state["last"] = {"at": stamp, "mode": mode, "done": done}
        print(f"[운영] {mode}: " + " / ".join(done))
    _save(state)
    return msgs if done else []   # 모드만 바뀌고 한 일이 없으면 조용히


def notify(msgs: list[str], now: datetime | None = None, post=None) -> None:
    if not msgs:
        return
    now = now or now_kst()
    if post is None:
        from .agents import _post_as
        post = lambda t: _post_as(NAME, t)   # noqa: E731
    post(f"-# ⚙️ 채우기 운영 · {now:%H:%M}\n" + "\n".join(msgs))


def status_text(now: datetime | None = None) -> str:
    now = now or now_kst()
    st = _load()
    last = st.get("last") or {}
    lines = [f"⚙️ **채우기 운영** - {MODE_LABEL.get(st.get('mode', ''), '아직 확인 전')}" + ("" if ENABLED else " (꺼짐, PORING_FILL=0)")]
    if st.get("since"):
        lines.append(f"• 이 모드 시작: {st['since'][11:16]}")
    if last:
        lines.append(f"• 마지막으로 한 일 ({last.get('at', '')[11:16]}): " + " / ".join(last.get("done") or []))
    if st.get("last_run"):
        lines.append(f"• 웹툰 전체 실행: {st['last_run'][5:16].replace('T', ' ')}")
    return "\n".join(lines)
