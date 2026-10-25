"""
결계 조절(부하 줄이기) - 서버 부하가 오래 높으면 그 순간 놀고 있는 컨테이너를 잠시 내리고, 부하가 내려가면 다시 올린다.

실제 서버를 건드리는 기능이다. 그래서:
- 내릴 수 있는 건 서버별 허용 목록(HOSTS의 "allow", .env로 바꿈)에 있는 컨테이너뿐이다. 목록에 없는 건 절대 안 건드린다.
- 다시 올리는 건 이 기능이 직접 내린 것뿐이다 (원래 꺼져 있던 컨테이너는 안 켠다).
- 내리는 건 그 순간 거의 일 안 하는 것만 (컨테이너 CPU < IDLE_CPU %).
- 기준: 서버 전체 CPU가 HIGH % 이상으로 HIGH_MIN분 이어지면 내림, LOW % 미만으로 LOW_MIN분 이어지면 다시 올림.
  내린 지 MAX_OFF_HOURS시간이 지나면 부하와 상관없이 다시 올리고, 다시 올린 컨테이너는 COOLDOWN_MIN분 동안 다시 안 내린다.
- 재는 법(봇 서버 etc에서): etc = /proc/stat 직접 + `docker stats`, plex01 = `ssh <host>`로 같은 것 (docker_bookoasis MCP가
  쓰는 SSH 접속 그대로). 내리기/올리기는 `docker stop` / `docker start`. 못 재면(SSH/도커 실패) 아무것도 안 한다.

이야기: 결계 마법사 지은탁(ACTOR)이 결계 부하를 줄이려고 그 감시탑(facilities.FACILITIES 이름)의 불을 잠시 끄고, 부하가 내려가면
다시 켠다 - 채널 한 줄 + 마을 사건. facilities.py는 이 기능이 끈 컨테이너를 "불 꺼짐" 사고로 치지 않는다 (quiet_names).
raids.py 위험도의 결계 부하는 두 서버 중 높은 쪽(max_cpu)을 쓴다.
봇(cogs/poring_food.py load_guard)이 CHECK_SEC(60초)마다 step()을 부르고, 한 일이 있으면 잠금 안에서 announce().
저장: storage/poring_food/loadshed.json {"hosts": {서버: {"samples": [[시각, cpu]], "shed": {이름: 시각},
"restored": {이름: 시각}, "last_cpu", "at"}}, "log": [...]}
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_LOADSHED", "1") not in ("0", "false", "False", "")
DRY_RUN = os.getenv("PORING_LOADSHED_DRY", "0") not in ("0", "false", "False", "")   # 1이면 내리고 올리는 척만 (로그만)
CHECK_SEC = 60
HIGH = float(os.getenv("PORING_LOADSHED_HIGH", "90"))
HIGH_MIN = int(os.getenv("PORING_LOADSHED_HIGH_MIN", "5"))
LOW = float(os.getenv("PORING_LOADSHED_LOW", "60"))
LOW_MIN = int(os.getenv("PORING_LOADSHED_LOW_MIN", "15"))
IDLE_CPU = float(os.getenv("PORING_LOADSHED_IDLE_CPU", "5"))
MAX_OFF_HOURS = float(os.getenv("PORING_LOADSHED_MAX_OFF_HOURS", "6"))
COOLDOWN_MIN = 30
KEEP_SAMPLES = 40
TIMEOUT = 25
ACTOR = "지은탁"
NAME = "💠 결계 조절"
LOADSHED_PATH = os.path.join(STATE_DIR, "loadshed.json")


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")   # 컨테이너 이름 (ssh 명령에 그대로 들어가서 검사)


def _names(env: str, default: str) -> list[str]:
    return [n.strip() for n in os.getenv(env, default).split(",") if _SAFE.match(n.strip())]


# 서버 -> ssh 대상("" = 이 봇이 도는 서버) / 내려도 되는 컨테이너
HOSTS = {
    "etc": {"ssh": "", "allow": _names("PORING_LOADSHED_ETC", "docker-warproxy")},
    "plex01": {"ssh": os.getenv("PORING_LOADSHED_PLEX_SSH", "192.168.0.31"),
               "allow": _names("PORING_LOADSHED_PLEX01", "tautulli")},
}


def _load() -> dict:
    try:
        with open(LOADSHED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(LOADSHED_PATH), exist_ok=True)
    tmp = LOADSHED_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOADSHED_PATH)


# ===================================================================== 재기 / 명령
def _cmd(host: str, args: list[str]) -> list[str]:
    ssh = HOSTS[host]["ssh"]
    if not ssh:
        return args
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh, " ".join(args)]


def _run(host: str, args: list[str]) -> str | None:
    """명령 실행 -> 표준 출력 (실패하면 None)."""
    try:
        p = subprocess.run(_cmd(host, args), capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[결계 조절] {host} 명령 실패: {' '.join(args)[:60]} - {e!r}"[:200])
        return None
    if p.returncode != 0:
        print(f"[결계 조절] {host} 명령 실패({p.returncode}): {' '.join(args)[:60]} - {p.stderr.strip()[:120]}")
        return None
    return p.stdout


def parse_stat(line: str) -> tuple[int, int] | None:
    """/proc/stat 첫 줄 -> (전체, 쉰 시간)."""
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    nums = [int(x) for x in parts[1:9]]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    return sum(nums), idle


def cpu_from(a: str, b: str) -> float | None:
    x, y = parse_stat(a), parse_stat(b)
    if not x or not y or y[0] <= x[0]:
        return None
    return round(100.0 * (1 - (y[1] - x[1]) / (y[0] - x[0])), 1)


def _host_cpu(host: str) -> float | None:
    if not HOSTS[host]["ssh"]:
        try:
            with open("/proc/stat") as f:
                a = f.readline()
            time.sleep(1)
            with open("/proc/stat") as f:
                b = f.readline()
        except OSError:
            return None
        return cpu_from(a, b)
    out = _run(host, ["head -1 /proc/stat; sleep 1; head -1 /proc/stat"])
    lines = (out or "").strip().splitlines()
    return cpu_from(lines[0], lines[1]) if len(lines) >= 2 else None


def parse_stats(out: str) -> dict[str, float]:
    """`docker stats --no-stream --format '{{.Name}}\\t{{.CPUPerc}}'` -> {이름: CPU %} (도는 것만 나온다)."""
    res = {}
    for line in (out or "").splitlines():
        name, _, pct = line.strip().partition("\t")
        try:
            res[name.strip()] = float(pct.strip().rstrip("%"))
        except ValueError:
            continue
    return res


def measure() -> dict:
    """[스레드에서] {서버: {"cpu": float|None, "running": {이름: CPU %}|None}}."""
    out = {}
    for host in HOSTS:
        cpu = _host_cpu(host)
        stats = _run(host, ["docker", "stats", "--no-stream", "--format", "'{{.Name}}\t{{.CPUPerc}}'"]
                     if HOSTS[host]["ssh"] else ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}"])
        out[host] = {"cpu": cpu, "running": parse_stats(stats) if stats is not None else None}
    return out


def _docker(host: str, do: str, name: str) -> bool:
    if DRY_RUN:
        print(f"[결계 조절] (시험) {host} docker {do} {name}")
        return True
    return _run(host, ["docker", do, name]) is not None


# ===================================================================== 판단 (순수)
def _sustained(samples: list, now: datetime, minutes: int, pred) -> bool:
    """최근 minutes분 동안 쭉 pred였나 (그만큼 기록이 쌓여 있어야 한다, 빠진 회차는 조금 봐준다)."""
    since = (now - timedelta(minutes=minutes)).isoformat()
    window = [c for t, c in samples if t >= since]
    covered = any(t <= (now - timedelta(minutes=minutes - 1)).isoformat() for t, _ in samples)
    return covered and len(window) >= max(1, int(minutes * 0.6)) and all(pred(c) for c in window)


def decide(host: str, st: dict, running: dict | None, now: datetime) -> list[dict]:
    """한 서버의 할 일 [{"host", "do": stop|start, "name", "why"}]. 순수 함수 (테스트용)."""
    samples = st.get("samples") or []
    shed = st.get("shed") or {}
    acts = []
    if shed:
        too_long = [n for n, t in shed.items() if t <= (now - timedelta(hours=MAX_OFF_HOURS)).isoformat()]
        if _sustained(samples, now, LOW_MIN, lambda c: c < LOW):
            return [{"host": host, "do": "start", "name": n, "why": f"결계 부하가 {LOW_MIN}분째 {LOW:g}% 아래"} for n in shed]
        acts += [{"host": host, "do": "start", "name": n, "why": f"{MAX_OFF_HOURS:g}시간 넘게 꺼져 있었음"} for n in too_long]
    if running is None or not _sustained(samples, now, HIGH_MIN, lambda c: c >= HIGH):
        return acts
    cool = (now - timedelta(minutes=COOLDOWN_MIN)).isoformat()
    for name in HOSTS[host]["allow"]:
        if name in shed or name not in running or (st.get("restored") or {}).get(name, "") >= cool:
            continue
        if running[name] < IDLE_CPU:
            acts.append({"host": host, "do": "stop", "name": name,
                         "why": f"결계 부하 {samples[-1][1]:g}% ({HIGH_MIN}분째 {HIGH:g}% 이상), 이 시설은 놀고 있음({running[name]:g}%)"})
    return acts


def step(now: datetime | None = None, measurer=None, docker=None) -> list[dict]:
    """[매분, 스레드에서] 재고 -> 판단 -> 실제로 내리고/올린다. 반환: 실제로 한 일."""
    if not ENABLED:
        return []
    now = now or now_kst()
    readings = (measurer or measure)()
    docker = docker or _docker
    data = _load()
    hosts = data.setdefault("hosts", {})
    done = []
    for host in HOSTS:
        r = readings.get(host) or {}
        st = hosts.setdefault(host, {})
        if r.get("cpu") is not None:
            st["samples"] = (st.get("samples") or [])[-(KEEP_SAMPLES - 1):] + [[now.isoformat(timespec="seconds"), r["cpu"]]]
            st["last_cpu"], st["at"] = r["cpu"], now.isoformat(timespec="seconds")
        for a in decide(host, st, r.get("running"), now):
            if not docker(host, a["do"], a["name"]):
                continue
            if a["do"] == "stop":
                st.setdefault("shed", {})[a["name"]] = now.isoformat(timespec="seconds")
            else:
                (st.get("shed") or {}).pop(a["name"], None)
                st.setdefault("restored", {})[a["name"]] = now.isoformat(timespec="seconds")
            a["cpu"] = st.get("last_cpu")
            done.append(a)
            print(f"[결계 조절] {host} {a['name']} {'내림' if a['do'] == 'stop' else '다시 올림'} - {a['why']}")
    if done:
        data["log"] = (data.get("log") or [])[-99:] + [{**a, "at": now.isoformat(timespec="minutes")} for a in done]
    _save(data)
    return done


# ===================================================================== 이야기 / 다른 모듈
def _label(name: str) -> str:
    from .facilities import FACILITIES
    return FACILITIES[name][0] if name in FACILITIES else name


def announce(done: list[dict], now: datetime | None = None, post=None, inject=None) -> None:
    """[잠금 안에서] 한 일을 이야기로: 채널 한 줄 + 마을 사건 + 지은탁 기억."""
    from . import agents as ag, memory
    now = now or now_kst()
    post = post or (lambda text: ag._post_as(NAME, text))
    inject = inject or ag.inject_event
    for a in done:
        label = _label(a["name"])
        if a["do"] == "stop":
            text = f"{ACTOR}이 결계 부하를 줄이려고 {label}의 불을 잠시 껐다 ({a['why']})."
            title = f"{label} 잠시 소등 (결계 조절)"
        else:
            text = f"{ACTOR}이 {label}에 다시 불을 켰다 ({a['why']})."
            title = f"{label} 다시 점등 (결계 조절)"
        post(f"-# {NAME} · {now:%H:%M} · {a['host']} `{a['name']}` {'⏸ 내림' if a['do'] == 'stop' else '▶ 다시 올림'}\n{text}")
        try:
            inject([ag.world.TOWN], text, title)
        except Exception as e:  # noqa: BLE001
            print(f"[결계 조절] 마을 사건 전달 실패: {e}")
        memory.remember(ACTOR, text, 5, [], "결계")


def quiet_names(now: datetime | None = None, minutes: int = 90) -> set[str]:
    """지금 이 기능이 꺼 둔 컨테이너 + 최근 다시 켠 컨테이너 (facilities가 사고로 치지 않게)."""
    now = now or now_kst()
    since = (now - timedelta(minutes=minutes)).isoformat()
    out = set()
    for st in (_load().get("hosts") or {}).values():
        out |= set(st.get("shed") or {})
        out |= {n for n, t in (st.get("restored") or {}).items() if t >= since}
    return out


def max_cpu(now: datetime | None = None, fresh_min: int = 15) -> float | None:
    """두 서버 중 높은 쪽의 최근 CPU % (raids 결계 부하용). 오래됐으면 None."""
    now = now or now_kst()
    since = (now - timedelta(minutes=fresh_min)).isoformat()
    vals = [st["last_cpu"] for st in (_load().get("hosts") or {}).values()
            if st.get("last_cpu") is not None and st.get("at", "") >= since]
    return max(vals) if vals else None


def summary(now: datetime | None = None) -> str:
    now = now or now_kst()
    data = _load()
    lines = [f"💠 **결계 조절** - {HIGH:g}% 이상 {HIGH_MIN}분이면 놀고 있는 시설을 내리고, {LOW:g}% 미만 {LOW_MIN}분이면 다시 올림"
             + (" (시험 모드)" if DRY_RUN else "")]
    for host, cfg in HOSTS.items():
        st = (data.get("hosts") or {}).get(host) or {}
        cpu = st.get("last_cpu")
        lines.append(f"• {host}: 부하 {f'{cpu:g}%' if cpu is not None else '모름'} · 내려도 되는 것 {', '.join(cfg['allow']) or '없음'}"
                     + (f" · 지금 내려 둔 것 {', '.join(st['shed'])}" if st.get("shed") else ""))
    for g in (data.get("log") or [])[-5:][::-1]:
        lines.append(f"  - {g['at'][5:16].replace('T', ' ')} {g['host']} {g['name']} {'내림' if g['do'] == 'stop' else '올림'} ({g['why']})")
    return "\n".join(lines)
