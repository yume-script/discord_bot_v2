# -*- coding: utf-8 -*-
"""
서버 상태 MCP - discord_bot_v2가 도는 서버("etc") 자체의 상태를 읽기 전용으로 노출한다.

[변경 이력]
1) 처음엔 mcp v2 API(mcp.server.mcpserver.MCPServer)로 짰다가 ModuleNotFoundError.
2) v1 API(FastMCP)로 고쳐서 poring_food의 venv로 돌리려 했지만, 그 venv의 mcp 버전을
   확인하지 않고 가정했고 실제로 도구가 로드되는 것도 확인하지 못했다.
3) 이번엔 life360/마인크래프트 MCP가 이미 잘 돌고 있는 discord_bot_v2 venv(mcp 1.30.0)에서
   돌리도록 옮겼다(yaml의 command를 그 venv의 python3로). 표준 라이브러리 + mcp만 쓴다.

[안전]
- 전부 읽기 전용이다. 쓰기/실행 도구는 없다.
- 프로세스 목록은 명령줄 전체가 아니라 프로세스 이름(comm)만 보여준다. 명령줄에는
  토큰/비밀번호가 인자로 들어 있는 경우가 있어서다.
- 서비스 이름은 정규식으로 검증한다(옵션 주입 방지, 최대 20개).

[환경변수(선택)]
  STATUS_DISK_PATHS  기본 "/,/mnt,/docker"  (같은 파티션이면 한 항목으로 합친다)
  STATUS_SERVICES    기본 "discord_bot_v2,cron,docker,ssh"
"""
import glob
import os
import re
import shutil
import socket
import subprocess
import time
from datetime import timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Server Status")

_DISK_PATHS = [p.strip() for p in os.getenv("STATUS_DISK_PATHS", "/,/mnt,/docker").split(",") if p.strip()]
_DEFAULT_SERVICES = [s.strip() for s in os.getenv("STATUS_SERVICES", "discord_bot_v2,cron,docker,ssh").split(",") if s.strip()]
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@._-]*$")
MAX_SERVICES = 20
_THERMAL_GLOB = os.getenv("STATUS_THERMAL_GLOB", "/sys/class/thermal/thermal_zone*")


def _gb(kb: int) -> float:
    return round(kb / 1024 / 1024, 2)


def _meminfo():
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    info[key] = int(parts[0])  # KB
    except (OSError, ValueError):
        return None, None
    total, avail = info.get("MemTotal", 0), info.get("MemAvailable", 0)
    mem = {
        "total_gb": _gb(total),
        "used_gb": _gb(total - avail),
        "available_gb": _gb(avail),
        "used_percent": round((total - avail) / total * 100, 1) if total else None,
    }
    s_total, s_free = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    swap = {
        "total_gb": _gb(s_total),
        "used_gb": _gb(s_total - s_free),
        "used_percent": round((s_total - s_free) / s_total * 100, 1) if s_total else None,
    }
    return mem, swap


def _cpu_times():
    """(idle, total, guest) 틱. guest(가상머신 실행 시간)는 user/nice에 이미 포함돼 있어 total에는 더하지 않는다."""
    with open("/proc/stat") as f:
        vals = list(map(int, f.readline().split()[1:]))
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    guest = (vals[8] if len(vals) > 8 else 0) + (vals[9] if len(vals) > 9 else 0)
    return idle, sum(vals[:8]), guest


_CGROUP_CPU_STAT = os.getenv("STATUS_CGROUP_CPU_STAT", "/sys/fs/cgroup/cpu.stat")


def _cgroup_usage_usec():
    """이 서버(컨테이너)가 쓴 누적 CPU 시간(usec). cgroup v2가 아니면 None."""
    try:
        with open(_CGROUP_CPU_STAT) as f:
            for line in f:
                if line.startswith("usage_usec"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _cpu_sample(interval: float = 0.5) -> dict:
    """CPU를 세 가지 관점으로 잰다.
    - host_percent: /proc/stat 기준. 이 서버가 LXC 컨테이너면 컨테이너가 아니라 '물리 머신 전체' 값이다.
    - vm_guest_percent: 그중 가상머신(KVM 게스트)이 쓴 몫. 이 서버 안에서 돌린 게 아니라 호스트의 다른 VM 것이다.
    - container_cores_used: cgroup 기준 '이 서버 자신'이 쓴 CPU(코어 1개 = 1.0).
    셋을 구분하지 않으면 다른 게스트의 부하를 이 서버 탓으로 오해하게 된다."""
    empty = {"host_percent": None, "vm_guest_percent": None, "container_cores_used": None}
    try:
        idle1, total1, guest1 = _cpu_times()
        usage1, t1 = _cgroup_usage_usec(), time.monotonic()
        time.sleep(interval)
        idle2, total2, guest2 = _cpu_times()
        usage2, t2 = _cgroup_usage_usec(), time.monotonic()
    except (OSError, ValueError, IndexError):
        return empty
    delta, elapsed = total2 - total1, t2 - t1
    result = dict(empty)
    if delta > 0:
        result["host_percent"] = round(100 * (1 - (idle2 - idle1) / delta), 1)
        result["vm_guest_percent"] = round(100 * (guest2 - guest1) / delta, 1)
    if usage1 is not None and usage2 is not None and elapsed > 0:
        result["container_cores_used"] = round((usage2 - usage1) / 1e6 / elapsed, 2)
    return result


def _usable_cores() -> int:
    try:
        return len(os.sched_getaffinity(0))  # cpuset으로 제한된 컨테이너면 허용된 코어 수
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def _disks() -> list:
    seen = {}
    gb = 1024 ** 3
    for path in _DISK_PATHS:
        try:
            dev = os.stat(path).st_dev
            usage = shutil.disk_usage(path)
        except OSError:
            continue  # 없는 경로는 건너뜀
        if dev in seen:
            seen[dev]["paths"].append(path)
            continue
        seen[dev] = {
            "paths": [path],
            "total_gb": round(usage.total / gb, 1),
            "used_gb": round(usage.used / gb, 1),
            "free_gb": round(usage.free / gb, 1),
            # df와 같은 공식: 사용량/(사용량+사용가능). used/total로 계산하면 ext4가 root용으로 예약한
            # 블록(기본 5%)이 여유 공간처럼 잡혀서 df(93%)보다 낮게(88.6%) 나온다.
            "used_percent": round(usage.used / (usage.used + usage.free) * 100, 1) if (usage.used + usage.free) else None,
        }
    return list(seen.values())


def _temperatures():
    """센서(thermal zone)별 온도. 최댓값 하나만 주면 어느 센서인지 몰라서 오해하기 쉽다
    (예: x86_pkg_temp는 CPU 패키지지만 acpitz 같은 값은 실제 열과 무관하게 고정값일 수 있음)."""
    zones = {}
    for d in sorted(glob.glob(_THERMAL_GLOB)):
        try:
            with open(os.path.join(d, "temp")) as f:
                value = round(int(f.read().strip()) / 1000, 1)
        except (OSError, ValueError):
            continue
        try:
            with open(os.path.join(d, "type")) as f:
                name = f.read().strip() or os.path.basename(d)
        except OSError:
            name = os.path.basename(d)
        if name in zones:  # 같은 타입의 센서가 여러 개면 구분
            name = f"{name}#{os.path.basename(d)}"
        zones[name] = value
    if not zones:
        return None
    return {"max": max(zones.values()), "zones": zones}


def _uptime():
    try:
        with open("/proc/uptime") as f:
            return str(timedelta(seconds=int(float(f.read().split()[0]))))
    except (OSError, ValueError):
        return None


def _cpu_summary(load) -> dict:
    cores = _usable_cores()
    cpu = _cpu_sample()
    used = cpu["container_cores_used"]
    return {
        "cores": cores,
        **cpu,
        "container_percent_of_machine": round(used / cores * 100, 1) if used is not None else None,
        "load_average": load,
    }


@mcp.tool()
def get_system_status() -> dict:
    """이 서버(etc, discord_bot_v2가 도는 서버)의 CPU, 메모리, 스왑, 디스크(파티션별), 온도(센서별,
    있을 때), 가동시간을 한 번에 조회합니다. CPU는 세 가지로 나뉩니다: host_percent(머신 전체 -
    이 서버가 컨테이너면 다른 게스트 부하까지 포함), vm_guest_percent(그중 가상머신이 쓴 몫),
    container_cores_used(이 서버 자신이 쓴 CPU, 코어 1개=1.0). 머신은 바쁜데 이 서버 몫이 낮으면
    부하의 원인은 이 서버 밖입니다."""
    mem, swap = _meminfo()
    try:
        load = dict(zip(("1min", "5min", "15min"), (round(x, 2) for x in os.getloadavg())))
    except OSError:
        load = None
    return {
        "hostname": socket.gethostname(),
        "uptime": _uptime(),
        "cpu": _cpu_summary(load),
        "memory": mem,
        "swap": swap,
        "disks": _disks(),
        "temperature_c": _temperatures(),
    }


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("systemctl") or "/usr/bin/systemctl"
    return subprocess.run([exe, *args], capture_output=True, text=True, timeout=5)


@mcp.tool()
def get_service_status(service_names: list[str] | None = None) -> dict:
    """systemd 서비스들의 상태(active/inactive/failed, 세부 상태, 시작 시각)를 확인합니다.
    service_names를 생략하면 기본 감시 대상(discord_bot_v2, cron, docker, ssh)을 확인합니다.
    존재하지 않는 서비스 이름은 'not-found'로 표시됩니다."""
    names = (service_names or _DEFAULT_SERVICES)[:MAX_SERVICES]
    result = {}
    for name in names:
        if not _SERVICE_RE.match(name):
            result[name] = {"error": "올바르지 않은 서비스 이름"}
            continue
        try:
            proc = _systemctl("show", name, "--property=LoadState,ActiveState,SubState,ActiveEnterTimestamp")
        except FileNotFoundError:
            result[name] = {"error": "systemctl을 찾을 수 없어요"}
            continue
        except subprocess.TimeoutExpired:
            result[name] = {"error": "systemctl 응답 시간 초과"}
            continue
        props = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        if not props:
            first = (proc.stderr.strip().splitlines() or ["알 수 없는 오류"])[0]
            result[name] = {"error": first}
        elif props.get("LoadState") == "not-found":
            result[name] = {"state": "not-found"}
        else:
            result[name] = {
                "state": props.get("ActiveState"),
                "sub_state": props.get("SubState"),
                "since": props.get("ActiveEnterTimestamp") or None,
            }
    return result


def _read_procs() -> dict:
    """pid -> (프로세스 이름, utime+stime 틱, RSS 페이지 수). /proc/<pid>/stat 하나로 읽는다."""
    procs = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                raw = f.read()
            left, right = raw.index("("), raw.rindex(")")  # 이름에 공백/괄호가 있어도 안전하게
            fields = raw[right + 2:].split()  # fields[0]이 stat의 3번째 필드(state)
            procs[int(entry)] = (raw[left + 1:right], int(fields[11]) + int(fields[12]), int(fields[21]))
        except (OSError, ValueError, IndexError):
            continue  # 그 사이에 종료된 프로세스 등
    return procs


@mcp.tool()
def get_top_processes(limit: int = 5, sort_by: str = "memory") -> list:
    """이 서버(컨테이너) 안의 프로세스 중 리소스를 많이 쓰는 상위 목록(이름만, 명령줄은 보안상 제외)을
    돌려줍니다. 다른 게스트/호스트의 프로세스는 보이지 않습니다.
    sort_by는 'memory' 또는 'cpu'. cpu_percent는 0.5초 동안 측정한 '지금'의 값이고 코어 1개를
    꽉 채우면 100%입니다(멀티코어라 100%를 넘을 수 있음)."""
    if sort_by not in ("memory", "cpu"):
        raise ValueError("sort_by는 'memory' 또는 'cpu'여야 해요.")
    n = max(1, min(int(limit or 5), 15))
    hz, page = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    mem, _swap = _meminfo()
    total_mb = (mem["total_gb"] * 1024) if mem else None

    before = _read_procs()
    t0 = time.monotonic()
    time.sleep(0.5)
    after = _read_procs()
    elapsed = max(time.monotonic() - t0, 0.001)

    rows = []
    for pid, (name, ticks, rss_pages) in after.items():
        prev = before.get(pid)
        cpu = max(ticks - prev[1], 0) / hz / elapsed * 100 if prev else 0.0
        rss_mb = rss_pages * page / 1024 / 1024
        rows.append({
            "pid": pid,
            "name": name,
            "cpu_percent": round(cpu, 1),
            "mem_percent": round(rss_mb / total_mb * 100, 1) if total_mb else None,
            "rss_mb": round(rss_mb, 1),
        })
    rows.sort(key=lambda r: r["cpu_percent"] if sort_by == "cpu" else r["rss_mb"], reverse=True)
    return rows[:n]


if __name__ == "__main__":
    mcp.run(transport="stdio")

