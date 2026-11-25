"""
서비스 응답 확인 - 사용자가 실제로 쓰는 곳(영상 재생·IPTV·책 읽기)이 지금 응답하는지 매분 직접 물어본다.

서버 조절(loadshed.py)은 부하만, 모니터링(facilities.py)은 docker 컨테이너가 켜져 있는지만 본다.
그래서 YM_PLAYER(systemd 서비스)나 Plex가 켜진 채로 응답을 멈추면 아무도 몰랐다 - 봇은 "YM_PLAYER 재생 없음"으로만 보고 지나갔다.
여기서는 각 서비스의 가벼운 주소(로그인 없이 열리는 상태 확인용)를 매분 불러 본다.

| 서비스               | 주소 (.env)                                   | 묻는 곳                    |
|----------------------|----------------------------------------------|----------------------------|
| YM_PLAYER            | PORING_YMPLAYER_URL                          | /api/auth/state            |
| Plex                 | PLEX_URL (기본 http://192.168.0.31:32400)      | /identity                  |
| 북오아시스            | PORING_BOOKOASIS_URL                         | / (로그인 화면으로 넘어가도 응답) |
| 북오아시스 어시스턴트 | PORING_ASSISTANT_URL                         | /healthz                   |
| 그 밖 (IPTV 등)       | PORING_UPTIME_EXTRA="이름=주소,이름=주소"       | 그 주소                     |
주소가 비어 있는 서비스는 확인하지 않는다 (/서비스상태에 "확인 안 함"으로 나온다).

- 응답 = HTTP 응답이 왔고 5xx가 아님 (로그인이 필요해 401/403/302여도 서버는 살아 있는 것).
- FAIL_N(3)번 연속(약 3분) 응답이 없으면 "⛔ 응답 없음"을 채널(🛰️ 모니터링)과 관리자 카톡방에 알린다.
  계속 안 되면 REPEAT_MIN(180분)마다 다시, 돌아오면 "✅ 다시 응답"을 한 번 (몇 분 동안 멈췄는지).
- 응답이 SLOW_SEC(5초)보다 느린 게 SLOW_N(5)번 이어지면 "🐢 느림"을 한 번 (빨라지면 조용히 풀림).
- 자동 복구 (PORING_UPTIME_AUTORESTART, 기본 켬): RESTART_N(3)번 연속(약 3분) 응답이 없으면 그 서비스만 다시 시작한다
  (plex01에 ssh - 서버 조절과 같은 PORING_LOADSHED_PLEX_SSH, 바꾸려면 PORING_UPTIME_RESTART_SSH).
  응답하지 않는 서비스는 이미 아무도 못 쓰고 있으므로 재생 여부와 상관없이. 다시 시작 사이 RESTART_GAP_MIN(15분),
  하루 RESTART_DAILY_MAX(3)번까지 - 넘으면 "자동 복구 한도"를 한 번 알리고 관리자에게 맡긴다.
  명령 (PORING_UPTIME_RESTART="이름=명령;이름=명령"으로 바꿈, 명령을 비우면 그 서비스는 자동 복구 안 함):
    YM_PLAYER              systemctl restart ym_player
    Plex                   systemctl restart plexmediaserver
    북오아시스             docker restart bookoasis
    북오아시스 어시스턴트   systemctl restart bookoasis_assistant
- PORING_UPTIME=0이면 끔. 카톡 알림은 PORING_HEALTH_ALERT를 따른다 (0이면 채널에만).
저장: storage/poring_food/uptime.json {"svc": {이름: {"fails", "slow", "down_since", "alerted", "slow_alerted", "ms", "at", "error"}}}
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import time
from datetime import datetime

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_UPTIME", "1") not in ("0", "false", "False", "")
UPTIME_PATH = os.path.join(STATE_DIR, "uptime.json")
TIMEOUT = float(os.getenv("PORING_UPTIME_TIMEOUT", "8"))
FAIL_N = int(os.getenv("PORING_UPTIME_FAIL_N", "3"))
SLOW_SEC = float(os.getenv("PORING_UPTIME_SLOW_SEC", "5"))
SLOW_N = 5
REPEAT_MIN = 180
NAME = "🛰️ 모니터링"
AUTORESTART = os.getenv("PORING_UPTIME_AUTORESTART", "1") not in ("0", "false", "False", "")
RESTART_N = max(2, int(os.getenv("PORING_UPTIME_RESTART_N", "3")))
RESTART_GAP_MIN = int(os.getenv("PORING_UPTIME_RESTART_GAP_MIN", "15"))
RESTART_DAILY_MAX = int(os.getenv("PORING_UPTIME_RESTART_DAILY_MAX", "3"))
RESTART_TIMEOUT = 120
_RESTART_DEFAULT = {"YM_PLAYER": "systemctl restart ym_player", "Plex": "systemctl restart plexmediaserver",
                    "북오아시스": "docker restart bookoasis", "북오아시스 어시스턴트": "systemctl restart bookoasis_assistant"}


def restart_cmds() -> dict:
    """서비스 이름 -> 다시 시작 명령 (기본 + .env PORING_UPTIME_RESTART로 바꾸거나 비워서 끄기)"""
    out = dict(_RESTART_DEFAULT)
    for part in os.getenv("PORING_UPTIME_RESTART", "").split(";"):
        name, eq, cmd = part.partition("=")
        if eq and name.strip():
            out[name.strip()] = cmd.strip()
    return {k: v for k, v in out.items() if v}


def _restart_ssh() -> str:
    ssh = os.getenv("PORING_UPTIME_RESTART_SSH", "").strip()
    if ssh:
        return ssh
    from . import loadshed
    return loadshed.HOSTS["plex01"]["ssh"]


def run_restart(cmd: str) -> tuple[bool, str]:
    """다시 시작 명령 실행 (plex01에 ssh, ssh 주소가 비면 이 서버에서) -> (성공, 오류)"""
    import subprocess
    ssh = _restart_ssh()
    args = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh, cmd] if ssh else ["sh", "-c", cmd]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=RESTART_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, repr(e)[:160]
    return p.returncode == 0, (p.stderr or p.stdout or "").strip()[-160:]


def _auto_restart(t: dict, s: dict, now: datetime, restarter) -> str:
    """응답 없는 서비스 다시 시작 (조건이 맞을 때만) -> 알릴 글 (없으면 "")"""
    cmd = restart_cmds().get(t["name"])
    if not AUTORESTART or not cmd or s.get("fails", 0) < RESTART_N:
        return ""
    stamp, today = now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d")
    if s.get("restart_day") != today:
        s.update(restart_day=today, restarts=0, restart_capped="")
    last = s.get("restart_at")
    if last and (now - datetime.fromisoformat(last)).total_seconds() < RESTART_GAP_MIN * 60:
        return ""
    if s["restarts"] >= RESTART_DAILY_MAX:
        if s.get("restart_capped"):
            return ""
        s["restart_capped"] = stamp
        _escalate("restart_cap", t["name"], f"오늘 {RESTART_DAILY_MAX}번 다시 시작(`{cmd}`)해도 응답 없음 - {t.get('what', '')}")
        return (f"🧯 **{t['name']}** 자동 복구 한도 - 오늘 {RESTART_DAILY_MAX}번 다시 시작해도 응답이 없어요. 관리자 확인이 필요합니다 "
                f"(`{cmd}`, 로그를 봐 주세요).")
    ok, err = restarter(cmd)
    s["restart_at"], s["restarts"] = stamp, s["restarts"] + 1
    print(f"[응답 확인] {t['name']} 자동 다시 시작 ({'성공' if ok else '실패'}): {cmd} {err}")
    if ok:
        return f"🔧 **{t['name']}** 응답이 없어 다시 시작했어요 (오늘 {s['restarts']}번째, `{cmd}`) - 다시 응답하면 알릴게요."
    _escalate("restart_failed", t["name"], f"`{cmd}` 실패: {err or '오류'}")
    return f"⚠️ **{t['name']}** 다시 시작하지 못했어요 (`{cmd}`): {err or '오류'}"


def _escalate(kind: str, service: str, text: str) -> None:
    """규칙으로 못 푼 문제 -> LLM 운영자(ops_agent)에게 진단을 맡긴다"""
    try:
        from . import ops_agent
        ops_agent.report(kind, service, text)
    except Exception as e:  # noqa: BLE001
        print(f"[응답 확인] 운영자에게 넘기지 못함: {e}")
_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]+)?(/[A-Za-z0-9._~/\-]*)?$")


def _base(env: str, default: str = "") -> str:
    u = (os.getenv(env, "").strip() or default).rstrip("/")
    return u if _SAFE_URL.match(u) else ""


def targets() -> list[dict]:
    """[{"name", "url", "what"}] - 주소가 있는 것만 url이 채워진다 (없으면 "")"""
    out = []
    for name, env, default, path, what in (
            ("YM_PLAYER", "PORING_YMPLAYER_URL", "", "/api/auth/state", "영상 재생·IPTV·라디오·책 보기"),
            ("Plex", "PLEX_URL", "http://192.168.0.31:32400", "/identity", "Plex 영상 재생"),
            ("북오아시스", "PORING_BOOKOASIS_URL", "", "/", "책·웹툰 읽기"),
            ("북오아시스 어시스턴트", "PORING_ASSISTANT_URL", "", "/healthz", "웹툰 받기·자료 등록")):
        base = _base(env, default)
        out.append({"name": name, "url": base + path if base else "", "what": what, "env": env})
    for part in os.getenv("PORING_UPTIME_EXTRA", "").split(","):
        name, _, url = part.partition("=")
        name, url = name.strip(), url.strip().rstrip("/")
        if name and _SAFE_URL.match(url) and not any(t["name"] == name for t in out):
            out.append({"name": name[:40], "url": url, "what": "추가 확인", "env": "PORING_UPTIME_EXTRA"})
    return out


def probe(url: str, get=None) -> tuple[bool, float, str]:
    """(응답했나, 걸린 초, 오류). 5xx·연결 실패·시간 초과는 실패. 리다이렉트는 따라가지 않는다 (302도 응답)"""
    t0 = time.monotonic()
    try:
        if get is None:
            import requests
            res = requests.get(url, timeout=TIMEOUT, allow_redirects=False, headers={"User-Agent": "poring-uptime"})
            code = res.status_code
        else:
            code = get(url)
    except Exception as e:  # noqa: BLE001
        msg = type(e).__name__
        if "Timeout" in msg:
            msg = f"{TIMEOUT:g}초 안에 응답 없음"
        elif "Connection" in msg:
            msg = "연결 안 됨 (꺼졌거나 포트가 닫힘)"
        return False, time.monotonic() - t0, msg
    took = time.monotonic() - t0
    if code >= 500:
        return False, took, f"HTTP {code}"
    return True, took, ""


def _load() -> dict:
    try:
        with open(UPTIME_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(UPTIME_PATH), exist_ok=True)
    tmp = UPTIME_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, UPTIME_PATH)


def _minutes(since: str, now: datetime) -> int:
    try:
        return max(1, round((now - datetime.fromisoformat(since)).total_seconds() / 60))
    except ValueError:
        return 0


def step(now: datetime | None = None, prober=None, tlist: list[dict] | None = None, restarter=None) -> list[str]:
    """[스레드에서, 매분] 모두 물어보고 상태를 고친다. 알릴 글들을 돌려준다 (없으면 [])."""
    now = now or now_kst()
    tlist = [t for t in (tlist if tlist is not None else targets()) if t["url"]]
    if not tlist:
        return []
    prober = prober or probe
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tlist)) as ex:
        results = dict(zip([t["name"] for t in tlist], ex.map(lambda t: prober(t["url"]), tlist)))
    data = _load()
    svc = data.setdefault("svc", {})
    stamp = now.isoformat(timespec="seconds")
    msgs = []
    for t in tlist:
        ok, took, err = results[t["name"]]
        s = svc.setdefault(t["name"], {})
        s.update(at=stamp, ms=round(took * 1000), error=err)
        if ok:
            if s.get("alerted"):
                msgs.append(f"✅ **{t['name']}** 다시 응답 ({t['what']}) - 약 {_minutes(s.get('down_since', stamp), now)}분 동안 멈춰 있었음")
                print(f"[응답 확인] {t['name']} 회복")
            s.update(fails=0, down_since="", alerted="")
            s["slow"] = s.get("slow", 0) + 1 if took > SLOW_SEC else 0
            if s["slow"] >= SLOW_N and not s.get("slow_alerted"):
                s["slow_alerted"] = stamp
                msgs.append(f"🐢 **{t['name']}** 응답이 느림 - {SLOW_N}분째 {SLOW_SEC:g}초 넘게 걸림 (지금 {took:.1f}초, {t['what']})")
            elif s["slow"] == 0:
                s["slow_alerted"] = ""
            continue
        s["fails"] = s.get("fails", 0) + 1
        if s["fails"] == 1:
            s["down_since"] = stamp
        day = now.strftime("%Y-%m-%d")   # 하루 운영 요약용 - 오늘 응답 없던 분 (매분 확인이라 1번 = 약 1분)
        s["down_min"] = (s.get("down_min", 0) if s.get("down_day") == day else 0) + 1
        s["down_day"] = day
        if s["fails"] < FAIL_N:
            continue
        last = s.get("alerted")
        stale = not last or (now - datetime.fromisoformat(last)).total_seconds() >= REPEAT_MIN * 60
        if stale:
            again = " (계속 멈춰 있음)" if last else ""
            msgs.append(f"⛔ **{t['name']}** 응답 없음{again} - {err} · 약 {_minutes(s['down_since'], now)}분째\n"
                        f"영향: {t['what']}이 안 될 수 있어요. (`{t['url']}`)")
            s["alerted"] = stamp
            print(f"[응답 확인] {t['name']} 응답 없음: {err}")
        fix = _auto_restart(t, s, now, restarter or run_restart)
        if fix:
            msgs.append(fix)
    # 설정에서 빠진 서비스 기록은 지운다
    for name in [n for n in svc if n not in {t["name"] for t in tlist}]:
        svc.pop(name, None)
    _save(data)
    return msgs


def notify(msgs: list[str], now: datetime | None = None, post=None, send=None) -> None:
    """채널(🛰️ 모니터링)과 관리자 카톡방에 알린다 (카톡은 PORING_HEALTH_ALERT가 켜져 있을 때)."""
    if not msgs:
        return
    now = now or now_kst()
    text = f"-# 🛰️ 서비스 응답 확인 · {now:%H:%M}\n" + "\n".join(msgs)
    if post is None:
        from .agents import _post_as
        post = lambda t: _post_as(NAME, t)   # noqa: E731
    post(text)
    from . import health
    if health.ENABLED:
        if send is None:
            from . import notifier
            send = notifier.send_to_local_bot
        try:
            send(re.sub(r"[*`]|-# ", "", text))
        except Exception as e:  # noqa: BLE001
            print(f"[응답 확인] 카톡 알림 실패: {e}")


def status_text(now: datetime | None = None) -> str:
    """/서비스상태 - 서비스마다 마지막 확인 결과"""
    now = now or now_kst()
    svc = _load().get("svc") or {}
    lines = [f"🛰️ **서비스 응답 확인** ({now:%H:%M}, 매분)" + ("" if ENABLED else " - 꺼져 있음 (PORING_UPTIME=0)")]
    for t in targets():
        if not t["url"]:
            lines.append(f"• {t['name']}: 확인 안 함 - 주소가 없어요 (.env `{t['env']}`)")
            continue
        s = svc.get(t["name"])
        if not s:
            lines.append(f"• {t['name']}: 아직 확인 전")
        elif s.get("fails"):
            lines.append(f"• ⛔ {t['name']}: 응답 없음 {s['fails']}번째 ({s.get('error')}) · {s.get('down_since', '')[11:16]}부터")
        else:
            slow = " 🐢" if s.get("slow", 0) >= SLOW_N else ""
            lines.append(f"• ✅ {t['name']}: {s.get('ms', 0)}ms{slow}")
        lines[-1] += f" - {t['what']}"
    lines.append(f"-# {FAIL_N}번 연속(약 {FAIL_N}분) 응답이 없으면 채널·카톡으로 알려요. 원본 주소로 바로 트는 IPTV는 PORING_UPTIME_EXTRA에 넣으면 같이 봐요.")
    return "\n".join(lines)
