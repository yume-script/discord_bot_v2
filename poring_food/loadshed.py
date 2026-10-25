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
import shlex
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


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")   # 컨테이너/서비스 이름 (명령에 들어가서 검사)
_SAFE_PROC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")  # 프로세스 이름 (pgrep -f 패턴)


def _names(env: str, default: str) -> list[str]:
    return [n.strip() for n in os.getenv(env, default).split(",") if _SAFE.match(n.strip())]


def _caps(env: str, default: str) -> dict[str, float]:
    """"flaskfarm:1,bookoasis_mariadb:2" -> {이름: CPU 상한(코어 수)}."""
    out = {}
    for part in os.getenv(env, default).split(","):
        name, _, cap = part.strip().partition(":")
        try:
            if _SAFE.match(name.strip()) and float(cap) > 0:
                out[name.strip()] = float(cap)
        except ValueError:
            continue
    return out


def _procs(env: str, default: str) -> list[str]:
    return [n.strip() for n in os.getenv(env, default).split(",") if _SAFE_PROC.match(n.strip())]


# 서버 -> ssh 대상("" = 이 봇이 도는 서버)
#  allow: 내려도 되는 컨테이너 (놀고 있을 때만 docker stop)
#  caps:  늦춰도 되는 컨테이너와 CPU 상한(코어 수) - 끄지 않고 docker update --cpus (CAP_MIN_CPU % 이상 쓰는 중일 때만)
#  units: 우선순위를 낮춰도 되는 systemd 서비스 - systemctl set-property --runtime CPUWeight=UNIT_WEIGHT
#  nice:  우선순위를 낮춰도 되는 프로세스 이름 - renice NICE_LEVEL (그때 떠 있는 것만)
HOSTS = {
    "etc": {"ssh": "", "docker_mcp": os.getenv("PORING_DOCKER_ETC_MCP_SERVER", "docker_local"), "allow": _names("PORING_LOADSHED_ETC", "docker-warproxy"),
            "caps": _caps("PORING_LOADSHED_ETC_CAP", ""), "units": _names("PORING_LOADSHED_ETC_UNITS", ""),
            "nice": _procs("PORING_LOADSHED_ETC_NICE", "")},
    "plex01": {"ssh": os.getenv("PORING_LOADSHED_PLEX_SSH", "192.168.0.31"),
               "docker_mcp": os.getenv("PORING_DOCKER_PLEX_MCP_SERVER", "docker_bookoasis"),
               "allow": _names("PORING_LOADSHED_PLEX01", "tautulli"),
               "caps": _caps("PORING_LOADSHED_PLEX01_CAP", "flaskfarm:1,bookoasis_mariadb:2"),
               "units": _names("PORING_LOADSHED_PLEX01_UNITS", "bookoasis_assistant.service"),
               "nice": _procs("PORING_LOADSHED_PLEX01_NICE", "Plex Media Scanner")},
}
CAP_MIN_CPU = 20      # 상한은 이만큼(%) 이상 쓰는 컨테이너에만 건다
UNIT_WEIGHT = 20      # systemd CPUWeight (기본 100)
NICE_LEVEL = 15


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


def _sh(host: str, script: str) -> str | None:
    """셸 한 줄 실행 (이 서버면 sh -c, 아니면 ssh 원격 셸)."""
    return _run(host, [script] if HOSTS[host]["ssh"] else ["sh", "-c", script])


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
    """한 서버의 할 일 [{"host", "do": stop|start|throttle|unthrottle, "name", "kind"?, "cap"?, "why"}]. 순수 함수 (테스트용).
    kind: cpus(컨테이너 CPU 상한) / unit(systemd CPUWeight) / nice(프로세스 우선순위)."""
    samples = st.get("samples") or []
    shed = st.get("shed") or {}
    slow = st.get("throttled") or {}
    cfg = HOSTS[host]
    acts = []
    if shed or slow:
        old = (now - timedelta(hours=MAX_OFF_HOURS)).isoformat()
        if _sustained(samples, now, LOW_MIN, lambda c: c < LOW):
            why = f"결계 부하가 {LOW_MIN}분째 {LOW:g}% 아래"
            return ([{"host": host, "do": "start", "name": n, "why": why} for n in shed]
                    + [{"host": host, "do": "unthrottle", "name": t["name"], "kind": t["kind"], "orig": t.get("orig"), "why": why}
                       for t in slow.values()])
        why = f"{MAX_OFF_HOURS:g}시간 넘게 그대로였음"
        acts += [{"host": host, "do": "start", "name": n, "why": why} for n, t in shed.items() if t <= old]
        acts += [{"host": host, "do": "unthrottle", "name": t["name"], "kind": t["kind"], "orig": t.get("orig"), "why": why}
                 for t in slow.values() if t.get("at", "") <= old]
    if running is None or not _sustained(samples, now, HIGH_MIN, lambda c: c >= HIGH):
        return acts
    cool = (now - timedelta(minutes=COOLDOWN_MIN)).isoformat()
    restored = st.get("restored") or {}
    high = f"결계 부하 {samples[-1][1]:g}% ({HIGH_MIN}분째 {HIGH:g}% 이상)"
    for name in cfg["allow"]:
        if name in shed or name not in running or restored.get(name, "") >= cool:
            continue
        if running[name] < IDLE_CPU:
            acts.append({"host": host, "do": "stop", "name": name, "why": f"{high}, 이 시설은 놀고 있음({running[name]:g}%)"})
    for kind, items in (("cpus", cfg.get("caps") or {}), ("unit", cfg.get("units") or []), ("nice", cfg.get("nice") or [])):
        for name in items:
            key = f"{kind}:{name}"
            if key in slow or restored.get(key, "") >= cool:
                continue
            if kind == "cpus":
                if name not in running or running[name] < CAP_MIN_CPU:
                    continue
                acts.append({"host": host, "do": "throttle", "name": name, "kind": kind, "cap": items[name],
                             "why": f"{high}, 이 시설이 {running[name]:g}%를 쓰는 중 - {items[name]:g}코어로 제한"})
            else:
                acts.append({"host": host, "do": "throttle", "name": name, "kind": kind, "why": f"{high} - 우선순위 낮춤"})
    return acts


def _throttle(host: str, a: dict) -> dict | None:
    """늦추기/되돌리기 실행. 반환: 성공하면 {"orig": 원래 값} (되돌리기는 {}), 아니면(대상이 없거나 실패) None."""
    name, kind = a["name"], a["kind"]
    q = shlex.quote(name)
    pat = shlex.quote(f"[{name[0]}]{name[1:]}")   # pgrep -f가 이 명령을 실행하는 셸 자신을 찾지 않게 ("[P]lex ...")
    if DRY_RUN:
        print(f"[결계 조절] (시험) {host} {a['do']} {kind} {name}")
        return {"orig": None}
    if a["do"] == "throttle":
        if kind == "cpus":
            orig = _run(host, ["docker", "inspect", "-f", "{{.HostConfig.NanoCpus}}", name])
            if orig is None or _run(host, ["docker", "update", "--cpus", f"{a['cap']:g}", name]) is None:
                return None
            return {"orig": int(orig.strip() or 0)}
        if kind == "unit":
            orig = _sh(host, f"systemctl show -p CPUWeight --value {q}")
            if _sh(host, f"systemctl set-property --runtime {q} CPUWeight={UNIT_WEIGHT}") is None:
                return None
            o = (orig or "").strip()
            return {"orig": int(o) if o.isdigit() else None}
        out = _sh(host, f"p=$(pgrep -f {pat}); if [ -n \"$p\" ]; then renice -n {NICE_LEVEL} -p $p >/dev/null && echo $p; fi")
        return {"orig": 0} if out and out.strip() else None
    if kind == "cpus":
        cpus = (a.get("orig") or 0) / 1e9
        return {} if _run(host, ["docker", "update", "--cpus", f"{cpus:g}", name]) is not None else None
    if kind == "unit":
        w = a.get("orig") or 100
        return {} if _sh(host, f"systemctl set-property --runtime {q} CPUWeight={w}") is not None else None
    _sh(host, f"p=$(pgrep -f {pat}); [ -n \"$p\" ] && renice -n 0 -p $p >/dev/null; true")
    return {}


def step(now: datetime | None = None, measurer=None, docker=None, throttler=None) -> list[dict]:
    """[매분, 스레드에서] 재고 -> 판단 -> 실제로 내리고/올리고/늦추고/되돌린다. 반환: 실제로 한 일."""
    if not ENABLED:
        return []
    now = now or now_kst()
    readings = (measurer or measure)()
    docker = docker or _docker
    throttler = throttler or _throttle
    data = _load()
    hosts = data.setdefault("hosts", {})
    done = []
    for host in HOSTS:
        r = readings.get(host) or {}
        st = hosts.setdefault(host, {})
        if r.get("cpu") is not None:
            st["samples"] = (st.get("samples") or [])[-(KEEP_SAMPLES - 1):] + [[now.isoformat(timespec="seconds"), r["cpu"]]]
            st["last_cpu"], st["at"] = r["cpu"], now.isoformat(timespec="seconds")
        if r.get("running") is not None:   # 매분 도는 컨테이너 목록 - facilities.quick이 사람이 끄고 켠 것도 바로 잡는다
            st["running"], st["running_at"] = sorted(r["running"]), now.isoformat(timespec="seconds")
        stamp = now.isoformat(timespec="seconds")
        for a in decide(host, st, r.get("running"), now):
            if a["do"] in ("stop", "start"):
                if not docker(host, a["do"], a["name"]):
                    continue
                if a["do"] == "stop":
                    st.setdefault("shed", {})[a["name"]] = stamp
                else:
                    (st.get("shed") or {}).pop(a["name"], None)
                    st.setdefault("restored", {})[a["name"]] = stamp
            else:
                got = throttler(host, a)
                if got is None:
                    continue
                key = f"{a['kind']}:{a['name']}"
                if a["do"] == "throttle":
                    st.setdefault("throttled", {})[key] = {"kind": a["kind"], "name": a["name"], "orig": got.get("orig"),
                                                           "at": stamp}
                else:
                    (st.get("throttled") or {}).pop(key, None)
                    st.setdefault("restored", {})[key] = stamp
            a["cpu"] = st.get("last_cpu")
            done.append(a)
            word = {"stop": "내림", "start": "다시 올림", "throttle": "늦춤", "unthrottle": "되돌림"}[a["do"]]
            print(f"[결계 조절] {host} {a['name']} {word} - {a['why']}")
    if done:
        data["log"] = (data.get("log") or [])[-99:] + [{**a, "at": now.isoformat(timespec="minutes")} for a in done]
    _save(data)
    return done


# ===================================================================== 이야기 / 다른 모듈
LABELS = {"bookoasis_assistant.service": "도서관 두루마리 운반 정령", "Plex Media Scanner": "환영극장 서고 정리 정령"}


def _label(name: str) -> str:
    from .facilities import FACILITIES
    return LABELS.get(name) or (FACILITIES[name][0] if name in FACILITIES else name)


def _story(a: dict) -> tuple[str, str, str]:
    """한 일 하나 -> (이야기 글, 사건 제목, 표시)."""
    from .josa import has_batchim, j
    label = _label(a["name"])
    ro = label + ("으로" if has_batchim(label) and (ord(label[-1]) - 0xAC00) % 28 != 8 else "로")   # ㄹ 받침은 "로"
    return {
        "stop": (f"{j(ACTOR, '이')} 결계 부하를 줄이려고 {label}의 불을 잠시 껐다 ({a['why']}).",
                 f"{label} 잠시 소등 (결계 조절)", "⏸ 내림"),
        "start": (f"{j(ACTOR, '이')} {label}에 다시 불을 켰다 ({a['why']}).", f"{label} 다시 점등 (결계 조절)", "▶ 다시 올림"),
        "throttle": (f"{j(ACTOR, '이')} 결계 부하를 줄이려고 {ro} 가는 마나 흐름을 좁혔다 ({a['why']}).",
                     f"{label} 마나 흐름 제한 (결계 조절)", "🐢 늦춤"),
        "unthrottle": (f"{j(ACTOR, '이')} {ro} 가는 마나 흐름을 원래대로 되돌렸다 ({a['why']}).",
                       f"{label} 마나 흐름 복구 (결계 조절)", "↩ 되돌림"),
    }[a["do"]]


def notify(done: list[dict], now: datetime | None = None, post=None) -> None:
    """[한 즉시, 잠금 없이] 채널에 바로 알린다 - 실제로 무엇을 했는지(서버/대상/동작/이유) + 이야기 한 줄."""
    from . import agents as ag
    now = now or now_kst()
    post = post or (lambda text: ag._post_as(NAME, text))
    for a in done:
        text, _, mark = _story(a)
        post(f"-# {NAME} · {now:%H:%M} · {a['host']} `{a['name']}` {mark}\n{text}")


# 시설 -> 그 일을 바로 겪는 기관 (그 기관 사람 몇 명이 지금 반응한다)
OWNERS = {"bookoasis_assistant.service": "BOOK", "Plex Media Scanner": "CINEMA"}


def key_people(names: list[str], agents: list[dict] | None = None, per_company: int = 2) -> list[str]:
    """바로 반응할 사람: 지은탁(한 사람) + 수비대장 리정혁 + 그 시설 기관 사람 몇 명."""
    from . import worldcfg
    from .agents import load_agents
    from .facilities import FACILITIES
    agents = agents if agents is not None else load_agents()
    out = [ACTOR, "리정혁"]
    for name in names:
        company = FACILITIES[name][1] if name in FACILITIES else getattr(worldcfg, OWNERS.get(name, ""), "")
        if company in worldcfg.OFFICES:
            out += [a["name"] for a in agents if a.get("company") == company][:per_company]
    return list(dict.fromkeys(out))


def reflect(done: list[dict], react=None) -> list[str]:
    """[잠금 안에서, notify 바로 뒤] 인물 세계에 바로 반영: 마을 사건 + 관련 인물이 지금 반응 + 지은탁 기억."""
    from . import agents as ag, memory
    react = react or ag.react_now
    reacted = []
    for a in done:
        text, title, _ = _story(a)
        try:
            reacted += react(text, title, key_people([a["name"]]))
        except Exception as e:  # noqa: BLE001
            print(f"[결계 조절] 세계 반영 실패: {e}")
        memory.remember(ACTOR, text, 5, [], "결계")
    return reacted


def announce(done: list[dict], now: datetime | None = None, post=None, react=None) -> None:
    """notify + reflect (테스트/단독 실행용 - 봇은 둘을 나눠 부른다: 알림은 바로, 반영은 잠금 안에서)."""
    notify(done, now, post)
    reflect(done, react)


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
    lines = [f"💠 **결계 조절** - {HIGH:g}% 이상 {HIGH_MIN}분이면 놀고 있는 시설은 내리고 바쁜 시설은 늦추고, "
             f"{LOW:g}% 미만 {LOW_MIN}분이면 되돌림" + (" (시험 모드)" if DRY_RUN else "")]
    word = {"stop": "내림", "start": "올림", "throttle": "늦춤", "unthrottle": "되돌림"}
    for host, cfg in HOSTS.items():
        st = (data.get("hosts") or {}).get(host) or {}
        cpu = st.get("last_cpu")
        slow = ", ".join(cfg["allow"] + [f"{n}(≤{c:g}코어)" for n, c in (cfg.get("caps") or {}).items()]
                         + list(cfg.get("units") or []) + list(cfg.get("nice") or []))
        lines.append(f"• {host}: 부하 {f'{cpu:g}%' if cpu is not None else '모름'} · 대상 {slow or '없음'}"
                     + (f" · 지금 내려 둔 것 {', '.join(st['shed'])}" if st.get("shed") else "")
                     + (f" · 지금 늦춘 것 {', '.join(t['name'] for t in st['throttled'].values())}" if st.get("throttled") else ""))
    for g in (data.get("log") or [])[-5:][::-1]:
        lines.append(f"  - {g['at'][5:16].replace('T', ' ')} {g['host']} {g['name']} {word.get(g['do'], g['do'])} ({g['why']})")
    return "\n".join(lines)


def running_by_server(now: datetime | None = None, fresh_sec: int = 150) -> dict[str, list[str]]:
    """방금 잰 도는 컨테이너 목록 {facilities 서버 키(docker MCP 이름): [이름]} (오래됐거나 못 쟀으면 빠진다)."""
    now = now or now_kst()
    since = (now - timedelta(seconds=fresh_sec)).isoformat(timespec="seconds")
    out = {}
    for host, st in (_load().get("hosts") or {}).items():
        if host in HOSTS and st.get("running_at", "") >= since and st.get("running") is not None:
            out[HOSTS[host]["docker_mcp"]] = list(st["running"])
    return out
