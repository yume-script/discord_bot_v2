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
  STATUS_TOTAL_FSTYPES 기본 "ext2,ext3,ext4,xfs,btrfs,zfs,f2fs" - 서버 전체 디스크(disk_total)에 넣는 로컬 파일시스템 종류
                       (네트워크/클라우드 마운트(nfs, cifs, fuse.rclone 등)와 tmpfs/overlay 같은 가상 파일시스템은 뺀다)
  STATUS_SERVICES    기본 "discord_bot_v2,cron,docker,ssh"
  STATUS_HOST_SSH    호스트(zeeps)에 붙는 ssh 대상 (기본 PORING_LOADSHED_ZEEPS_SSH). 봇 키는 호스트의
                     village-ctl.sh에 묶여 있어 "top"(게스트별·프로세스별 CPU, 읽기만)만 부른다.
                     MCP는 stdio로 떠서 봇의 환경변수를 물려받지 못하므로 봇의 .env 파일에서도 읽는다.

[부하는 호스트 기준]
이 서버(etc)는 호스트 zeeps 위의 LXC 컨테이너라 안에서는 다른 게스트(CT/VM)와 호스트의 프로세스가 안 보인다.
그래서 "왜 바쁜가"는 get_host_load(호스트 전체: 게스트별 합계 + 상위 프로세스)로 답한다.
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


def _dotenv(key: str) -> str:
    """봇의 .env(이 파일의 한 단계 위)에서 값 하나 (MCP는 봇의 환경변수를 물려받지 못한다)"""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                k, sep, v = line.strip().partition("=")
                if sep and k.strip() == key:
                    v = re.split(r"\s+#", v, maxsplit=1)[0].strip().strip("'\"")
                    return v
    except OSError:
        pass
    return ""


def _host_ssh() -> str:
    for key in ("STATUS_HOST_SSH", "PORING_LOADSHED_ZEEPS_SSH"):
        v = (os.getenv(key) or _dotenv(key)).strip()
        if v:
            return v
    return ""

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


_TOTAL_FSTYPES = {t.strip() for t in os.getenv("STATUS_TOTAL_FSTYPES", "ext2,ext3,ext4,xfs,btrfs,zfs,f2fs").split(",")
                  if t.strip()}
_SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/snap", "/boot/efi")


def _disk_total() -> dict | None:
    """서버 전체 디스크: /proc/mounts의 로컬 파일시스템을 전부(같은 장치는 한 번) 합친 사용률.
    파티션 하나가 차 있어도 서버 전체 창고가 찬 건 아니라서, 이야기 속 '지하 공용 창고 적재율'은 이 값을 쓴다."""
    seen, total, used, free, mounts = set(), 0, 0, 0, []
    try:
        with open("/proc/mounts") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt, fstype = parts[1].replace("\\040", " "), parts[2]
        if fstype not in _TOTAL_FSTYPES or mnt.startswith(_SKIP_PREFIXES):
            continue
        try:
            dev = os.stat(mnt).st_dev
            u = shutil.disk_usage(mnt)
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        total, used, free = total + u.total, used + u.used, free + u.free
        mounts.append(mnt)
    if not mounts:
        return None
    gb = 1024 ** 3
    return {"mounts": mounts, "total_gb": round(total / gb, 1), "used_gb": round(used / gb, 1),
            "free_gb": round(free / gb, 1),
            "used_percent": round(used / (used + free) * 100, 1) if (used + free) else None}


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


def parse_top(out: str) -> dict | None:
    """village-ctl.sh top 출력 -> {"cores", "guests": [{kind, id, name, cpu_percent}], "processes": [{guest, name, cpu_percent}]}"""
    cores, guests, procs = None, [], []
    for line in (out or "").splitlines():
        parts = line.split()
        try:
            if parts[:1] == ["cores"] and len(parts) == 2:
                cores = int(parts[1])
            elif parts[:1] == ["guest"] and len(parts) == 3:
                kind, gid, name = (parts[1].split(":", 2) + ["", ""])[:3]
                guests.append({"kind": kind, "id": gid, "name": name, "cpu_percent": float(parts[2])})
            elif parts[:1] == ["proc"] and len(parts) >= 4:
                kind, gid, name = (parts[1].split(":", 2) + ["", ""])[:3]
                procs.append({"guest": f"{kind}:{gid}:{name}", "name": " ".join(parts[2:-1]), "cpu_percent": float(parts[-1])})
        except (ValueError, IndexError):
            continue
    if cores is None and not guests:
        return None
    guests.sort(key=lambda g: g["cpu_percent"], reverse=True)
    procs.sort(key=lambda p: p["cpu_percent"], reverse=True)
    return {"cores": cores, "guests": guests, "processes": procs}


_GUEST_WORD = {"ct": "컨테이너", "vm": "가상머신", "host": "호스트 자체"}


def _host_top() -> dict:
    """호스트에서 '어디서 CPU를 쓰는가' (ssh 실패/미설정이면 available False와 이유)"""
    ssh = _host_ssh()
    if not ssh:
        return {"available": False, "reason": "호스트 접속(STATUS_HOST_SSH 또는 PORING_LOADSHED_ZEEPS_SSH)이 설정되지 않음"}
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", ssh, "top"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "reason": f"호스트에 접속하지 못함: {type(e).__name__}"}
    data = parse_top(p.stdout) if p.returncode == 0 else None
    if not data:
        err = (p.stderr.strip().splitlines() or [""])[-1][:160]
        return {"available": False, "reason": "호스트가 top을 지원하지 않음 (scripts/zeeps/install.sh를 다시 실행해 village-ctl.sh 갱신 필요)"
                if "허용되지 않은" in err else f"호스트 응답을 읽지 못함: {err or p.returncode}"}
    return {"available": True, **data}


def _host_summary(top: dict, host_percent) -> str:
    """사람이 읽는 한 줄: 호스트 CPU와 가장 많이 쓰는 게스트·프로세스"""
    if not top.get("available"):
        return ""
    cores = top.get("cores") or 1
    parts = []
    for g in top["guests"][:3]:
        word = _GUEST_WORD.get(g["kind"], g["kind"])
        label = g["name"] if g["kind"] == "host" else f"{g['name']}({word} {g['id']})"
        parts.append(f"{label} {g['cpu_percent'] / cores:.0f}%")
    procs = [f"{p['name']}@{p['guest'].split(':')[-1]} {p['cpu_percent'] / cores:.0f}%" for p in top["processes"][:3]]
    head = f"호스트 전체 CPU {host_percent:g}%" if host_percent is not None else "호스트 CPU"
    return head + (" - 많이 쓰는 곳: " + ", ".join(parts) if parts else "") + (" · 프로세스: " + ", ".join(procs) if procs else "")


@mcp.tool()
def get_host_load() -> dict:
    """호스트(zeeps) 전체 기준으로 지금 CPU를 어디서 쓰는지 알려 줍니다. 부하의 원인을 묻거나 부하를 이야기할 때는 이 도구를 씁니다.
    - host_percent: 물리 머신 전체 CPU 사용률(%)
    - guests: 게스트(ct=LXC 컨테이너, vm=가상머신, host=호스트 자체)별 CPU. cpu_percent는 코어 1개=100,
      machine_percent는 머신 전체 대비 %
    - processes: 호스트 전체 상위 프로세스(이름만, 어느 게스트 안인지 guest로). 명령줄은 보안상 없음
    - summary: 한 줄 요약
    이 봇이 도는 서버(etc)도 게스트 중 하나일 뿐이다. '이 서버 밖이라 모른다'고 답하지 말고 여기 나온 게스트·프로세스를 원인으로 말한다."""
    cpu = _cpu_sample()
    top = _host_top()
    cores = (top.get("cores") if top.get("available") else None) or os.cpu_count() or 1
    if top.get("available"):
        for g in top["guests"]:
            g["machine_percent"] = round(g["cpu_percent"] / cores, 1)
        for p in top["processes"]:
            p["machine_percent"] = round(p["cpu_percent"] / cores, 1)
        top["processes"] = top["processes"][:15]
    return {"host_percent": cpu["host_percent"], "vm_guest_percent": cpu["vm_guest_percent"], "cores": cores,
            **top, "summary": _host_summary(top, cpu["host_percent"])}


def _host_load_brief() -> dict:
    top = _host_top()
    if not top.get("available"):
        return {"available": False, "reason": top.get("reason", "")}
    cores = top.get("cores") or 1
    return {"available": True,
            "guests": [{"guest": f"{g['kind']}:{g['id']}:{g['name']}", "machine_percent": round(g["cpu_percent"] / cores, 1)}
                       for g in top["guests"][:5]],
            "processes": [{"guest": p["guest"], "name": p["name"], "machine_percent": round(p["cpu_percent"] / cores, 1)}
                          for p in top["processes"][:5]]}


@mcp.tool()
def get_system_status() -> dict:
    """이 서버(etc, discord_bot_v2가 도는 서버)의 CPU, 메모리, 스왑, 디스크(파티션별), 온도(센서별,
    있을 때), 가동시간을 한 번에 조회합니다. CPU는 세 가지로 나뉩니다: host_percent(머신 전체 -
    이 서버가 컨테이너면 다른 게스트 부하까지 포함), vm_guest_percent(그중 가상머신이 쓴 몫),
    container_cores_used(이 서버 자신이 쓴 CPU, 코어 1개=1.0). 서버 부하는 호스트 기준(host_percent)으로 말하고,
    원인은 host_load(호스트 전체의 게스트별·프로세스별 CPU)에서 찾는다 - 더 자세히는 get_host_load.
    disk_total은 서버의 로컬 디스크 전체를 합친 사용률입니다 (disks는 STATUS_DISK_PATHS 파티션별)."""
    mem, swap = _meminfo()
    try:
        load = dict(zip(("1min", "5min", "15min"), (round(x, 2) for x in os.getloadavg())))
    except OSError:
        load = None
    return {
        "hostname": socket.gethostname(),
        "uptime": _uptime(),
        "cpu": _cpu_summary(load),
        "host_load": _host_load_brief(),
        "memory": mem,
        "swap": swap,
        "disks": _disks(),
        "disk_total": _disk_total(),
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
    """이 서버(etc 컨테이너) 안의 프로세스 중 리소스를 많이 쓰는 상위 목록(이름만, 명령줄은 보안상 제외)을
    돌려줍니다. 다른 게스트/호스트의 프로세스는 보이지 않으므로, CPU 부하의 원인을 찾을 때는 get_host_load를 씁니다.
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

