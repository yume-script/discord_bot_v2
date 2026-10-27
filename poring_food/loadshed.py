"""
결계 조절(부하 줄이기) - 「집스마을 서버 관리 가이드 v1.0」의 단계(0~4) 방식.

목표 위계 (절대 바뀌지 않는다):
 1) 영상 재생(Plex/YM_PLAYER/SHYNI)과 책·웹툰 열람(북오아시스)이 끊기거나 느려지지 않는다 - 절대 목표.
 2) 부하 지수를 95% 아래로 - 1번을 지키는 안전선.
 3) 자료(웹툰/책)를 최대한 많이 DB에 올린다 - 1·2번이 허락하는 만큼만. 그래서 짐꾼(bookoasis_assistant)은 끄지 않고
    3단계부터 늦추기만 한다(진행 중인 회차는 끝까지 올라가게).

안전장치:
- PROTECTED(티어 S 시청 경로 + 금고/통신망)는 설정에 적혀 있어도 절대 안 건드린다 - 도구 자체를 안 만든다.
  bookoasis/bookoasis_mariadb/bookoasis_redis, Plex(스캐너 포함), shyni, YM_PLAYER, flaskfarm(IPTV 경로라 컨테이너 단위 금지),
  rclone 마운트, vaultwarden, litellm.
- 모든 조치는 되돌릴 수 있고(원래 값 기록), ttl이 지나면 부하와 상관없이 되돌린다. 복구는 조치의 역순.
- 이 기능이 직접 한 것만 되돌린다 (원래 꺼져 있던 컨테이너는 안 켠다). 못 재면 아무것도 안 한다.

부하 지수 = max(CPU us+sy+irq+steal (nice 제외 - nice된 작업은 바로 양보하므로), 메모리 사용 %, I/O 압박 PSI some avg10).
- 서버별로 매분 잰다: etc = 이 서버 /proc, plex01 = `ssh <host>`. PORING_LOADSHED_ZEEPS_SSH가 있으면 호스트(zeeps)도 재서
  두 서버 지수에 같이 반영한다(호스트 부하는 모든 CT에 걸리므로).
- 단계는 최근 5분 평균으로 정한다 (1분만 튀면 관찰만): 60/70/80/90% 이상 = 1/2/3/4단계.
  재생 중(Plex 세션이 있음)이면 기준을 10%p 낮춘다. PSI(cpu>10, io>10, memory>1)가 있으면 한 단계 올린다.
- 한 번에 한 단계씩 올리고(5분 간격), 5분 평균이 90%(재생 중 80%) 이상이면 바로 4단계.
- 내릴 때는 지금 단계 기준보다 5%p 낮은 값이 10분 이어져야 한 단계 내린다 (히스테리시스).

단계별 조치 (STAGE_PLAN, .env로 바꿈 - 보호 목록은 못 넘는다):
- 1단계~: 놀고 있는(CPU < IDLE_CPU %) 희생 가능 컨테이너 정지 - 기본 etc docker-warproxy. ttl STOP_TTL_H.
- 3단계~: 짐꾼 bookoasis_assistant.service CPUWeight 30, 4단계면 15 (끄지 않음). ttl SLOW_TTL_H.
          rclone 업로드만 대역폭 제한(PORING_LOADSHED_RCLONE_RC가 있을 때, rate=5M:off - 읽기/재생은 안 막는다).
- 4단계 또는 지수 95% 초과: 채널에 ⚠️ 경고.
- 방역관(ClamAV, 호스트 zeeps): PORING_LOADSHED_ZEEPS_SSH가 있으면 매분 호스트의 scripts/zeeps/village-ctl.sh에 "stage N"을 보낸다
  (봇 키는 authorized_keys command=로 이 스크립트에 묶여 probe/stage/status 말고는 못 한다). 호스트는
  /run/village/stage를 쓰고(가이드 6.3 게이트 - 방역/정비 타이머가 ExecCondition으로 읽고 스스로 건너뜀),
  1단계~ 방역관 CPUQuota 25%, 2단계~ 돌고 있는 정기 스캔(risk/deep) 중단, 0단계면 60%로. 실시간 감시(malware-watch)는 유지.

이야기: 결계 무녀 슈나(ACTOR)가 결계 경보 단계를 올리고 내리며, 짐 나르는 정령의 마나 흐름을 좁히고 성문 초소 불을 잠시 끈다.
facilities.py는 이 기능이 끈 컨테이너를 "불 꺼짐" 사고로 치지 않는다 (quiet_names). raids 위험도는 max_cpu()(가장 높은 지수).
봇(cogs/poring_food.py load_guard)이 CHECK_SEC(60초)마다 step()을 부르고, 한 일이 있으면 바로 notify, 잠금 안에서 reflect.
저장: storage/poring_food/loadshed.json {"hosts": {서버: {"samples": [[시각, 지수, psi]], "stage", "stage_at", "shed": {이름: 시각},
"throttled": {키: {...}}, "restored": {키: 시각}, "last_cpu"(=지수), "at", "playing"}}, "log": [...]}
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_LOADSHED", "1") not in ("0", "false", "False", "")
DRY_RUN = os.getenv("PORING_LOADSHED_DRY", "0") not in ("0", "false", "False", "")   # 1이면 하는 척만 (로그만)
CHECK_SEC = 60
STAGES = [60.0, 70.0, 80.0, 90.0]           # 1~4단계 시작 지수
PLAY_ADJ = 10.0                             # 재생 중이면 기준을 이만큼 낮춘다
DOWN_GAP = 5.0                              # 내릴 때 기준보다 이만큼 더 낮아야
UP_EVERY_MIN = 5                            # 한 단계 올린 뒤 다음 단계까지 (조치 효과 검증 시간)
DOWN_MIN = 10                               # 낮은 값이 이만큼 이어져야 한 단계 내림
AVG_MIN = 5                                 # 단계 판단은 최근 5분 평균
ALERT = 95.0
IDLE_CPU = float(os.getenv("PORING_LOADSHED_IDLE_CPU", "5"))
STOP_TTL_H = float(os.getenv("PORING_LOADSHED_MAX_OFF_HOURS", "6"))
SLOW_TTL_H = float(os.getenv("PORING_LOADSHED_SLOW_HOURS", "2"))
COOLDOWN_MIN = 30
KEEP_SAMPLES = 40
TIMEOUT = 25
ACTOR = "슈나"
NAME = "💠 결계 조절"
LOADSHED_PATH = os.path.join(STATE_DIR, "loadshed.json")
UNIT_WEIGHT = {3: 30, 4: 15}                # 짐꾼 CPUWeight (기본 100) - 끄지 않고 늦춘다 (자료 등록이 3순위 목표라 너무 굶기지 않게)
RCLONE_RATE = os.getenv("PORING_LOADSHED_RCLONE_RATE", "5M:off")   # 업로드:다운로드 - 다운로드(재생)는 off(제한 없음)

_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")   # 컨테이너/서비스 이름 (명령에 들어가서 검사)
_SAFE_URL = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]+)?$")

# 티어 S(시청 경로) + 특별 보호 - 어떤 단계, 어떤 설정에서도 정지/강등하지 않는다
PROTECTED = {"bookoasis", "bookoasis_mariadb", "bookoasis_redis", "flaskfarm", "shyni", "plex", "plexmediaserver",
             "plexmediaserver.service", "ym_player", "vaultwarden", "litellm-proxy", "litellm-db", "gds2.service",
             "pve-cluster.service", "docker.service", "containerd.service"}
_PROTECTED_PREFIX = ("plex", "rclone", "litellm", "vaultwarden", "ym_player", "ym-player", "shyni", "bookoasis_mariadb",
                     "bookoasis_redis", "mariadb", "mysql", "redis", "nginx", "pve")


def protected(name: str) -> bool:
    n = (name or "").strip().lower()
    return n in PROTECTED or n.startswith(_PROTECTED_PREFIX)


def _names(env: str, default: str) -> list[str]:
    """"docker-warproxy, x" -> 안전한 이름만, 보호 대상은 뺀다 (로그로 알림)."""
    out = []
    for n in os.getenv(env, default).split(","):
        n = n.strip()
        if not _SAFE.match(n):
            continue
        if protected(n):
            print(f"[결계 조절] {env}의 {n}은(는) 시청 경로/보호 대상이라 조치 목록에서 뺐다")
            continue
        out.append(n)
    return out


def _url(env: str) -> str:
    u = os.getenv(env, "").strip().rstrip("/")
    return u if _SAFE_URL.match(u) else ""


# 서버 -> ssh 대상("" = 이 봇이 도는 서버)
#  stop:  1단계부터 놀고 있으면 내려도 되는 희생 가능(티어 B) 컨테이너
#  units: 3단계부터 늦출 systemd 서비스(티어 A 짐꾼) - CPUWeight 20(3단계)/5(4단계), 끄지 않는다
#  rclone: 3단계부터 업로드만 대역폭 제한할 rclone rc 주소 (예: http://192.168.0.10:5276)
HOSTS = {
    "etc": {"ssh": "", "docker_mcp": os.getenv("PORING_DOCKER_ETC_MCP_SERVER", "docker_local"),
            "stop": _names("PORING_LOADSHED_ETC", "docker-warproxy"), "units": _names("PORING_LOADSHED_ETC_UNITS", ""),
            "rclone": ""},
    "plex01": {"ssh": os.getenv("PORING_LOADSHED_PLEX_SSH", "192.168.0.31"),
               "docker_mcp": os.getenv("PORING_DOCKER_PLEX_MCP_SERVER", "docker_bookoasis"),
               "stop": _names("PORING_LOADSHED_PLEX01", ""),
               "units": _names("PORING_LOADSHED_PLEX01_UNITS", "bookoasis_assistant.service"),
               "rclone": _url("PORING_LOADSHED_RCLONE_RC")},
}
ZEEPS_SSH = os.getenv("PORING_LOADSHED_ZEEPS_SSH", "").strip()      # 호스트(zeeps) 지수도 같이 볼 때 (읽기만)
STAGE_SSH = os.getenv("PORING_LOADSHED_STAGE_SSH", ZEEPS_SSH).strip()   # village-ctl.sh가 있는 호스트 (기본 = ZEEPS_SSH)
STOP_STAGE = 1
SLOW_STAGE = 3


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
def _ssh_of(host: str) -> str:
    return ZEEPS_SSH if host == "zeeps" else HOSTS[host]["ssh"]


def _cmd(host: str, args: list[str]) -> list[str]:
    ssh = _ssh_of(host)
    if not ssh:
        return args
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh, " ".join(args)]


def _sh(host: str, script: str) -> str | None:
    """셸 한 줄 실행 (이 서버면 sh -c, 아니면 ssh 원격 셸)."""
    return _run(host, [script] if _ssh_of(host) else ["sh", "-c", script])


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


def parse_stat(line: str) -> tuple[int, int, int] | None:
    """/proc/stat 첫 줄 -> (전체, 쉰 시간, 바쁜 시간 - nice/iowait 제외)."""
    parts = line.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    nums = [int(x) for x in parts[1:9]] + [0] * 8
    user, nice, system, idle, iowait, irq, softirq, steal = nums[:8]
    return sum(nums[:8]), idle + iowait, user + system + irq + softirq + steal


def cpu_from(a: str, b: str) -> float | None:
    """전체 CPU 사용률 (nice 포함 - 표시용)."""
    x, y = parse_stat(a), parse_stat(b)
    if not x or not y or y[0] <= x[0]:
        return None
    return round(100.0 * (1 - (y[1] - x[1]) / (y[0] - x[0])), 1)


def busy_from(a: str, b: str) -> float | None:
    """지수용 CPU = us+sy+irq+steal (nice된 작업은 바로 양보하므로 위험으로 안 센다)."""
    x, y = parse_stat(a), parse_stat(b)
    if not x or not y or y[0] <= x[0]:
        return None
    return round(100.0 * (y[2] - x[2]) / (y[0] - x[0]), 1)


def parse_mem(text: str) -> float | None:
    vals = {}
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable") and v.split():
            vals[k] = int(v.split()[0])
    if vals.get("MemTotal") and "MemAvailable" in vals:
        return round(100.0 * (1 - vals["MemAvailable"] / vals["MemTotal"]), 1)
    return None


def parse_psi(line: str) -> float | None:
    """'some avg10=1.23 avg60=... ' -> 1.23."""
    m = re.search(r"some\s+avg10=([0-9.]+)", line or "")
    return float(m.group(1)) if m else None


_PROBE = ("head -1 /proc/stat; sleep 1; head -1 /proc/stat; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo; "
          "for r in cpu io memory; do printf 'psi_%s ' $r; head -1 /proc/pressure/$r 2>/dev/null || echo; done")


def parse_probe(out: str) -> dict | None:
    """_PROBE 출력 -> {"cpu"(nice 포함), "busy", "mem", "psi": {cpu, io, memory}, "index"}."""
    lines = (out or "").strip().splitlines()
    stats = [ln for ln in lines if ln.startswith("cpu ")]
    if len(stats) < 2:
        return None
    busy, mem = busy_from(stats[0], stats[1]), parse_mem("\n".join(lines))
    if busy is None:
        return None
    psi = {}
    for ln in lines:
        if ln.startswith("psi_"):
            kind, _, rest = ln[4:].partition(" ")
            psi[kind] = parse_psi(rest)
    io = psi.get("io")
    index = max(v for v in (busy, mem, io) if v is not None)
    return {"cpu": cpu_from(stats[0], stats[1]), "busy": busy, "mem": mem, "psi": psi, "index": round(index, 1),
            "raw": stats[1]}


def smooth(probe: dict | None, store: dict, stamp: str, since: str) -> dict | None:
    """CPU를 1초 순간값 대신 '지난 측정부터 지금까지'(보통 1분) 평균으로 바꾼다 - 1초짜리는 44%와 99%를 오가서
    단계가 출렁이고 못 내려왔다. store에 마지막 /proc/stat 줄을 둔다. 지난 줄이 since보다 오래됐으면 1초 값 그대로."""
    if not probe or not probe.get("raw"):
        return probe
    prev, prev_at = store.get("raw"), store.get("raw_at", "")
    store["raw"], store["raw_at"] = probe["raw"], stamp
    busy = busy_from(prev, probe["raw"]) if prev and prev_at >= since else None
    if busy is None:
        return probe
    io = (probe.get("psi") or {}).get("io")
    index = max(v for v in (busy, probe.get("mem"), io) if v is not None)
    return {**probe, "busy": busy, "cpu": cpu_from(prev, probe["raw"]), "index": round(index, 1)}


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


def _playing() -> int | None:
    """지금 재생 중인 수 (Plex 직접 -> 매시 지표). 모르면 None."""
    try:
        from . import plex_api
        if plex_api.enabled():
            got = plex_api.sessions()
            if got is not None:
                return len(got)
        from . import metrics
        return metrics.streams()
    except Exception:  # noqa: BLE001
        return None


def measure() -> dict:
    """[스레드에서] {서버: {"probe": dict|None, "running": {이름: CPU %}|None}, "_zeeps": dict|None, "_playing": int|None}."""
    out = {}
    for host in HOSTS:
        probe = parse_probe(_sh(host, _PROBE) or "")
        stats = _run(host, ["docker", "stats", "--no-stream", "--format", "'{{.Name}}\t{{.CPUPerc}}'"]
                     if HOSTS[host]["ssh"] else ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}"])
        out[host] = {"probe": probe, "running": parse_stats(stats) if stats is not None else None}
    # 호스트는 village-ctl.sh(강제 명령) 뒤에 있다 - 셸 대신 "probe"만 보낸다
    out["_zeeps"] = parse_probe(_run("zeeps", ["probe"]) or "") if ZEEPS_SSH else None
    out["_playing"] = _playing()
    return out


# ===================================================================== 단계 판단 (순수)
def psi_alarm(psi: dict | None) -> bool:
    psi = psi or {}
    return (psi.get("cpu") or 0) > 10 or (psi.get("io") or 0) > 10 or (psi.get("memory") or 0) > 1


def stage_of(value: float, playing: bool) -> int:
    adj = PLAY_ADJ if playing else 0.0
    return sum(1 for t in STAGES if value >= t - adj)


def next_stage(samples: list, current: int, changed_at: str, playing: bool, now: datetime) -> tuple[int, str]:
    """최근 기록으로 다음 단계와 이유. samples = [[시각, 지수, psi경보(bool)]]. 순수 함수 (테스트용)."""
    since5 = (now - timedelta(minutes=AVG_MIN)).isoformat()
    recent = [s for s in samples if s[0] >= since5]
    if len(recent) < 3:
        return current, ""
    avg = sum(s[1] for s in recent) / len(recent)
    alarm = bool(recent[-1][2]) if len(recent[-1]) > 2 else False
    target = min(4, stage_of(avg, playing) + (1 if alarm else 0))
    adj = PLAY_ADJ if playing else 0.0
    tag = f"5분 평균 {avg:.0f}%" + (" · 재생 중이라 기준 -10%p" if playing else "") + (" · 지연(PSI) 감지" if alarm else "")
    waited = not changed_at or changed_at <= (now - timedelta(minutes=UP_EVERY_MIN)).isoformat()
    if target > current:
        if avg >= STAGES[3] - adj:
            return 4, tag + " - 급등이라 바로 4단계"
        return (current + 1, tag) if waited else (current, "")
    if target < current and current > 0:
        floor = STAGES[current - 1] - adj - DOWN_GAP
        since10 = (now - timedelta(minutes=DOWN_MIN)).isoformat()
        window = [s for s in samples if s[0] >= since10]
        covered = any(s[0] <= (now - timedelta(minutes=DOWN_MIN - 1)).isoformat() for s in samples)
        # PSI 경보는 "한 단계 더"일 뿐이다 - 경보가 계속 있어도(예: 다른 작업이 CPU를 붙잡고 있음) 지수+1이 지금보다 낮으면 내려간다
        # (예전엔 경보가 한 번이라도 있으면 못 내려가서, 4단계에 갇힌 채 짐꾼이 계속 묶여 있었다)
        # 내릴 때도 10분 평균으로 본다 (한 번 튄 값 때문에 몇 시간씩 못 내려오지 않게). 경보는 절반 넘게 울렸을 때만 +1.
        avg10 = sum(s[1] for s in window) / len(window) if window else 999.0
        alarmed = sum(1 for s in window if len(s) > 2 and s[2]) * 2 > len(window)
        calm = covered and len(window) >= DOWN_MIN * 0.6 and avg10 < floor and \
            stage_of(avg10, playing) + (1 if alarmed else 0) < current
        long_enough = not changed_at or changed_at <= since10
        if calm and long_enough:
            return current - 1, f"{DOWN_MIN}분 평균 {avg10:.0f}% (기준 {floor:g}% 아래)"
    return current, ""


def plan(host: str, stage: int, st: dict, running: dict | None, now: datetime) -> list[dict]:
    """지금 단계에 맞춰 할 일 [{"host", "do": stop|start|throttle|unthrottle, "name", "kind"?, "weight"?, "why"}].
    복구는 조치의 역순(마지막에 한 것부터). ttl이 지나면 단계와 상관없이 되돌린다. 순수 함수 (테스트용)."""
    cfg = HOSTS[host]
    shed = st.get("shed") or {}
    slow = st.get("throttled") or {}
    restored = st.get("restored") or {}
    cool = (now - timedelta(minutes=COOLDOWN_MIN)).isoformat()
    stop_old = (now - timedelta(hours=STOP_TTL_H)).isoformat()
    slow_old = (now - timedelta(hours=SLOW_TTL_H)).isoformat()
    undo = []
    for n, t in shed.items():
        stale = n not in cfg["stop"] or protected(n)        # 예전 설정으로 내린 것 / 이제 보호 대상 -> 바로 되돌림
        if stale or stage < STOP_STAGE or t <= stop_old:
            undo.append((t, {"host": host, "do": "start", "name": n,
                             "why": "이제 조치 대상이 아님" if stale else
                             f"결계 경보 {stage}단계로 내려감" if stage < STOP_STAGE else f"{STOP_TTL_H:g}시간 지남(ttl)"}))
    for key, t in slow.items():
        stale = t["kind"] not in ("unit", "rclone") or protected(t["name"]) or \
            (t["kind"] == "unit" and t["name"] not in cfg["units"])
        if stale or stage < SLOW_STAGE or t.get("at", "") <= slow_old:
            undo.append((t.get("at", ""), {"host": host, "do": "unthrottle", "name": t["name"], "kind": t["kind"],
                                            "orig": t.get("orig"),
                                            "why": "시청 경로/이제 조치 대상이 아님" if stale else
                                            f"결계 경보 {stage}단계로 내려감" if stage < SLOW_STAGE else f"{SLOW_TTL_H:g}시간 지남(ttl)"}))
    acts = [a for _, a in sorted(undo, key=lambda x: x[0], reverse=True)]
    undone = {a["name"] for a in acts}
    if running is None:
        return acts
    why = f"결계 경보 {stage}단계"
    if stage >= STOP_STAGE:
        for name in cfg["stop"]:
            if protected(name) or name in shed or name not in running or restored.get(name, "") >= cool:
                continue
            if running[name] < IDLE_CPU:
                acts.append({"host": host, "do": "stop", "name": name, "why": f"{why}, 이 시설은 놀고 있음({running[name]:g}%)"})
    if stage >= SLOW_STAGE:
        for name in cfg["units"]:
            key, w = f"unit:{name}", UNIT_WEIGHT[min(stage, 4)]
            if protected(name) or name in undone or restored.get(key, "") >= cool:
                continue
            if (slow.get(key) or {}).get("weight") != w:
                acts.append({"host": host, "do": "throttle", "name": name, "kind": "unit", "weight": w,
                             "why": f"{why} - 끄지 않고 늦춤(진행 중인 회차는 끝까지)"})
        if cfg.get("rclone"):
            key = "rclone:upload"
            if key not in slow and "upload" not in undone and restored.get(key, "") < cool:
                acts.append({"host": host, "do": "throttle", "name": "upload", "kind": "rclone", "weight": RCLONE_RATE,
                             "why": f"{why} - 업로드만 {RCLONE_RATE} (재생 읽기는 그대로)"})
    return acts


def _docker(host: str, do: str, name: str) -> bool:
    if protected(name):
        return False
    if DRY_RUN:
        print(f"[결계 조절] (시험) {host} docker {do} {name}")
        return True
    return _run(host, ["docker", do, name]) is not None


def _rclone(host: str, rate: str) -> bool:
    import requests
    url = HOSTS[host].get("rclone")
    if not url:
        return False
    if DRY_RUN:
        print(f"[결계 조절] (시험) rclone bwlimit {rate}")
        return True
    try:
        return requests.post(f"{url}/core/bwlimit", json={"rate": rate}, timeout=8).status_code == 200
    except requests.RequestException as e:
        print(f"[결계 조절] rclone bwlimit 실패: {e!r}"[:200])
        return False


def _throttle(host: str, a: dict) -> dict | None:
    """늦추기/되돌리기 실행. 반환: 성공하면 {"orig": 원래 값} (되돌리기는 {}), 아니면 None."""
    name, kind = a["name"], a["kind"]
    if a["do"] == "throttle" and (kind not in ("unit", "rclone") or protected(name)):
        return None
    if DRY_RUN:
        print(f"[결계 조절] (시험) {host} {a['do']} {kind} {name} {a.get('weight', '')}")
        return {"orig": None}
    q = shlex.quote(name)
    if kind == "rclone":
        return ({"orig": "off"} if _rclone(host, str(a["weight"])) else None) if a["do"] == "throttle" else \
            ({} if _rclone(host, "off") else None)
    if a["do"] == "throttle":
        orig = a.get("orig_known")
        if orig is None:
            o = (_sh(host, f"systemctl show -p CPUWeight --value {q}") or "").strip()
            orig = int(o) if o.isdigit() else None
        if _sh(host, f"systemctl set-property --runtime {q} CPUWeight={int(a['weight'])}") is None:
            return None
        return {"orig": orig}
    if kind == "cpus":      # 예전 버전이 건 컨테이너 CPU 상한 되돌리기 (이제는 걸지 않는다)
        cpus = (a.get("orig") or 0) / 1e9
        return {} if _run(host, ["docker", "update", "--cpus", f"{cpus:g}", name]) is not None else None
    if kind == "nice":      # 예전 버전이 낮춘 프로세스 우선순위 되돌리기
        pat = shlex.quote(f"[{name[0]}]{name[1:]}")
        _sh(host, f"p=$(pgrep -f {pat}); [ -n \"$p\" ] && renice -n 0 -p $p >/dev/null; true")
        return {}
    w = a.get("orig") or 100
    return {} if _sh(host, f"systemctl set-property --runtime {q} CPUWeight={w}") is not None else None


CLAM_LABEL = "방역관의 정화 의식(ClamAV)"


def parse_ctl(out: str, stage: int) -> list[dict]:
    """village-ctl.sh "stage N" 출력 -> 한 일 [{"host": "zeeps", ...}] (바뀐 것만 나온다)."""
    done, quota = [], ""
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "stopped":
            done.append({"host": "zeeps", "do": "stop", "name": parts[1],
                         "why": f"결계 경보 {stage}단계 - 정기 스캔은 다음 타이머 때 다시 (실시간 감시는 유지)"})
        elif len(parts) >= 3 and parts[0] == "quota":
            quota = parts[2]
    if quota:
        slow = quota != "60%"
        done.append({"host": "zeeps", "do": "throttle" if slow else "unthrottle", "name": "clamav", "kind": "quota",
                     "why": f"결계 경보 {stage}단계 - 방역관 CPU {quota}로"})
    return done


def _write_stage(stage: int) -> list[dict]:
    """[매분] 가이드 6.3 게이트 + 방역관 조절: 호스트 village-ctl.sh에 "stage N" (호스트 타이머가 ExecCondition으로 읽는다).
    반환: 호스트에서 실제로 바뀐 것 (방역관 CPU 제한/정기 스캔 중단)."""
    if not STAGE_SSH or DRY_RUN:
        return []
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", STAGE_SSH, f"stage {int(stage)}"],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[결계 조절] 호스트 단계 전달 실패: {e!r}"[:200])
        return []
    if p.returncode != 0:
        print(f"[결계 조절] 호스트 단계 전달 실패({p.returncode}): {p.stderr.strip()[:120]}")
        return []
    return parse_ctl(p.stdout, stage)


def step(now: datetime | None = None, measurer=None, docker=None, throttler=None, stage_writer=None) -> list[dict]:
    """[매분, 스레드에서] 재고 -> 단계 -> 실제로 내리고/올리고/늦추고/되돌린다. 반환: 실제로 한 일 (단계 변화 포함)."""
    if not ENABLED:
        return []
    now = now or now_kst()
    readings = (measurer or measure)()
    docker = docker or _docker
    throttler = throttler or _throttle
    data = _load()
    hosts = data.setdefault("hosts", {})
    stamp0 = now.isoformat(timespec="seconds")
    since_raw = (now - timedelta(minutes=5)).isoformat(timespec="seconds")
    zeeps = smooth(readings.get("_zeeps") or None, data.setdefault("zeeps", {}), stamp0, since_raw)
    playing = readings.get("_playing")
    done = []
    stamp = now.isoformat(timespec="seconds")
    for host in HOSTS:
        r = readings.get(host) or {}
        st = hosts.setdefault(host, {})
        probe = smooth(r.get("probe"), st, stamp, since_raw)
        if probe is None and r.get("cpu") is not None:      # 예전 모양 (cpu만) - 지수로 그대로 쓴다
            probe = {"index": r["cpu"], "cpu": r["cpu"], "psi": {}}
        if probe is not None:
            index = max(probe["index"], (zeeps or {}).get("index") or 0)
            alarm = psi_alarm(probe.get("psi")) or psi_alarm((zeeps or {}).get("psi"))
            # 예전 버전 기록([시각, 전체 CPU(nice 포함)])은 지수가 아니라서 버린다 - 안 버리면 배포 직후 옛 값으로 단계가 튄다
            kept = [x for x in (st.get("samples") or []) if len(x) >= 3]
            st["samples"] = kept[-(KEEP_SAMPLES - 1):] + [[stamp, index, alarm]]
            st["last_cpu"], st["at"] = index, stamp
            st["detail"] = {k: probe.get(k) for k in ("cpu", "busy", "mem")} | {"psi": probe.get("psi") or {}}
        if r.get("running") is not None:   # 매분 도는 컨테이너 목록 - facilities.quick이 사람이 끄고 켠 것도 바로 잡는다
            st["running"], st["running_at"] = sorted(r["running"]), stamp
        st["playing"] = playing
        if probe is None:          # 못 쟀으면 단계는 그대로, 아무것도 안 한다
            continue
        cur = int(st.get("stage", 0))
        new, why = next_stage(st.get("samples") or [], cur, st.get("stage_at", ""), bool(playing), now)
        if new != cur:
            st["stage"], st["stage_at"] = new, stamp
            done.append({"host": host, "do": "stage", "name": "결계 경보", "from": cur, "to": new, "why": why,
                         "cpu": st["last_cpu"], "alert": new >= 4 or st["last_cpu"] > ALERT})
            print(f"[결계 조절] {host} 결계 경보 {cur} -> {new}단계 - {why}")
        for a in plan(host, new, st, r.get("running"), now):
            if a["do"] in ("stop", "start"):
                if not docker(host, a["do"], a["name"]):
                    continue
                if a["do"] == "stop":
                    st.setdefault("shed", {})[a["name"]] = stamp
                else:
                    (st.get("shed") or {}).pop(a["name"], None)
                    st.setdefault("restored", {})[a["name"]] = stamp
            else:
                key = f"{a['kind']}:{a['name']}"
                prev = (st.get("throttled") or {}).get(key)
                if a["do"] == "throttle" and prev:
                    a["orig_known"] = prev.get("orig")
                got = throttler(host, a)
                a.pop("orig_known", None)
                if got is None:
                    continue
                if a["do"] == "throttle":
                    st.setdefault("throttled", {})[key] = {"kind": a["kind"], "name": a["name"], "weight": a.get("weight"),
                                                           "orig": prev.get("orig") if prev else got.get("orig"),
                                                           "at": prev.get("at", stamp) if prev else stamp}
                else:
                    (st.get("throttled") or {}).pop(key, None)
                    st.setdefault("restored", {})[key] = stamp
            a["cpu"] = st.get("last_cpu")
            done.append(a)
            word = {"stop": "내림", "start": "다시 올림", "throttle": "늦춤", "unthrottle": "되돌림"}[a["do"]]
            print(f"[결계 조절] {host} {a['name']} {word} - {a['why']}")
    top = max((int(st.get("stage", 0)) for h, st in hosts.items() if h in HOSTS), default=0)
    for a in (stage_writer or _write_stage)(top):
        a["cpu"] = (zeeps or {}).get("index")
        done.append(a)
        print(f"[결계 조절] zeeps {a['name']} {'늦춤' if a['do'] == 'throttle' else '되돌림' if a['do'] == 'unthrottle' else '중단'} - {a['why']}")
    if done:
        data["log"] = (data.get("log") or [])[-99:] + [{**a, "at": now.isoformat(timespec="minutes")} for a in done]
    _save(data)
    return done


# ===================================================================== 이야기 / 다른 모듈
LABELS = {"bookoasis_assistant.service": "도서관 두루마리 운반 정령", "upload": "왕도로 가는 두루마리 짐마차",
          "clamav": CLAM_LABEL, "clamav-risk-scan.service": "방역관의 매시 정화 의식", "clamav-deep-scan.service": "방역관의 새벽 대정화 의식"}
STAGE_NAMES = {0: "평시", 1: "경계", 2: "방어", 3: "짐꾼 늦춤", 4: "짐꾼 최소"}


def _label(name: str) -> str:
    from .facilities import FACILITIES
    return LABELS.get(name) or (FACILITIES[name][0] if name in FACILITIES else name)


def _story(a: dict) -> tuple[str, str, str]:
    """한 일 하나 -> (이야기 글, 사건 제목, 표시)."""
    from .josa import has_batchim, j
    if a["do"] == "stage":
        up = a["to"] > a["from"]
        name = STAGE_NAMES.get(a["to"], "")
        if up:
            text = (f"{j(ACTOR, '이')} 결계 경보를 {a['to']}단계({name})로 올렸다 ({a['why']}). "
                    "환영극과 책 열람은 그대로 지키고, 짐 나르는 일과 정비부터 쉬게 한다.")
        else:
            text = f"{j(ACTOR, '이')} 결계 경보를 {a['to']}단계({name})로 내렸다 ({a['why']}). 쉬던 일이 차례로 돌아온다."
        return text, f"결계 경보 {a['to']}단계 ({name})", ("⚠️ " if a.get("alert") else "") + ("⬆" if up else "⬇") + f" {a['to']}단계"
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
        warn = "\n⚠️ 부하 지수가 높아요 - 시청 경로는 건드리지 않았고, 계속 높으면 직접 확인이 필요해요." if a.get("alert") else ""
        post(f"-# {NAME} · {now:%H:%M} · {a['host']} `{a['name']}` {mark}\n{text}{warn}")


# 시설 -> 그 일을 바로 겪는 기관 (그 기관 사람 몇 명이 지금 반응한다)
OWNERS = {"bookoasis_assistant.service": "BOOK", "upload": "BOOK"}


def key_people(names: list[str], agents: list[dict] | None = None, per_company: int = 2) -> list[str]:
    """바로 반응할 사람: 슈나(한 사람) + 수비대장 베니마루 + 그 시설 기관 사람 몇 명."""
    from . import worldcfg
    from .agents import load_agents
    from .facilities import FACILITIES
    agents = agents if agents is not None else load_agents()
    out = [ACTOR, "베니마루"] + (["애순이"] if "결계 경보" in names else [])   # 단계는 총괄 애순이도 바로 안다
    for name in names:
        company = FACILITIES[name][1] if name in FACILITIES else getattr(worldcfg, OWNERS.get(name, ""), "")
        if company in worldcfg.OFFICES:
            out += [a["name"] for a in agents if a.get("company") == company][:per_company]
    return list(dict.fromkeys(out))


def reflect(done: list[dict], react=None) -> list[str]:
    """[잠금 안에서, notify 바로 뒤] 인물 세계에 바로 반영: 마을 사건 + 관련 인물이 지금 반응 + 슈나 기억."""
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


def stage(now: datetime | None = None, fresh_min: int = 15) -> int:
    """지금 가장 높은 결계 경보 단계 (오래됐으면 0)."""
    now = now or now_kst()
    since = (now - timedelta(minutes=fresh_min)).isoformat()
    return max([int(st.get("stage", 0)) for st in (_load().get("hosts") or {}).values() if st.get("at", "") >= since] or [0])


def summary(now: datetime | None = None) -> str:
    now = now or now_kst()
    data = _load()
    lines = [f"💠 **결계 조절** (지수 = max(CPU nice 제외, 메모리, I/O 압박), 5분 평균 {'/'.join(f'{t:g}' for t in STAGES)}% = 1~4단계, "
             f"재생 중 -{PLAY_ADJ:g}%p) - 시청 경로(북오아시스·Plex·YM_PLAYER·SHYNI·flaskfarm)는 절대 안 건드림"
             + (" (시험 모드)" if DRY_RUN else "")]
    word = {"stop": "내림", "start": "올림", "throttle": "늦춤", "unthrottle": "되돌림", "stage": "단계"}
    for host, cfg in HOSTS.items():
        st = (data.get("hosts") or {}).get(host) or {}
        idx, det = st.get("last_cpu"), st.get("detail") or {}
        parts = [f"{n}(1단계~ 정지)" for n in cfg["stop"]] + [f"{n}(3단계~ 늦춤)" for n in cfg["units"]] \
            + (["rclone 업로드(3단계~ 제한)"] if cfg.get("rclone") else [])
        lines.append(f"• {host}: {int(st.get('stage', 0))}단계({STAGE_NAMES[int(st.get('stage', 0))]}) · 지수 "
                     + (f"{idx:g}%" if idx is not None else "모름")
                     + (f" (CPU {det.get('busy')}% · 메모리 {det.get('mem')}% · I/O {(det.get('psi') or {}).get('io')})" if det else "")
                     + (f" · 재생 {st['playing']}" if st.get("playing") else "")
                     + f" · 대상 {', '.join(parts) or '없음'}"
                     + (f" · 지금 내려 둔 것 {', '.join(st['shed'])}" if st.get("shed") else "")
                     + (f" · 지금 늦춘 것 {', '.join(t['name'] for t in st['throttled'].values())}" if st.get("throttled") else ""))
    for g in (data.get("log") or [])[-5:][::-1]:
        what = f"{g.get('from')}→{g.get('to')}단계" if g.get("do") == "stage" else f"{g['name']} {word.get(g['do'], g['do'])}"
        lines.append(f"  - {g['at'][5:16].replace('T', ' ')} {g['host']} {what} ({g['why']})")
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
