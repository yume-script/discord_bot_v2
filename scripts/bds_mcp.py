# -*- coding: utf-8 -*-
"""
Minecraft Bedrock(itzg/minecraft-bedrock-server) 콘솔 MCP 서버.

[왜 이렇게 만들었나]
Bedrock 서버는 RCON을 지원하지 않고, 이미지에 들어 있는 send-command 스크립트는
컨테이너가 서버 프로세스의 /proc/<PID>/exe를 읽지 못해서 실패한다(uid를 바꿔도 동일).
대신 이 컨테이너는 stdin/tty가 열려 있고(OpenStdin=true, StdinOnce=false), 컨테이너
stdin이 서버 콘솔로 전달된다. 그래서 Docker Engine API의 attach(stdin)로 명령을 쓰고,
응답은 컨테이너 로그에서 "명령을 보낸 시각 이후의 줄"만 골라서 돌려준다.
컨테이너 권한을 늘리거나(SYS_PTRACE 등) 재시작할 필요가 없다.

[안전장치]
- 임의 명령 전송 도구(bds_send_command)는 제거했다 - 조회 도구(상태/접속자/최근 로그)만
  노출한다. 서버 콘솔에 보내는 명령은 내부에서 쓰는 고정 명령("list")뿐이다.
- 컨테이너는 BDS_CONTAINER 하나에만 붙는다. 외부 라이브러리 없이 표준 라이브러리로
  Docker 소켓과 직접 통신한다(의존성 충돌 없음).

[환경변수]
  BDS_CONTAINER         기본 mc-be-server
  DOCKER_SOCK           기본 /var/run/docker.sock
"""
import http.client
import json
import os
import re
import socket
import struct
import time
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Minecraft BDS")

CONTAINER = os.getenv("BDS_CONTAINER", "mc-be-server")
DOCKER_SOCK = os.getenv("DOCKER_SOCK", "/var/run/docker.sock")
if not re.fullmatch(r"[A-Za-z0-9_.-]+", CONTAINER):
    raise SystemExit(f"BDS_CONTAINER 값이 올바르지 않습니다: {CONTAINER!r}")


# --------------------------------------------------------------------------
# Docker Engine API (unix socket, 표준 라이브러리만 사용)
# --------------------------------------------------------------------------
class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = 10):
        super().__init__("docker", timeout=timeout)
        self._path = path

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self._path)
        self.sock = s


def _docker_error(exc: Exception) -> RuntimeError:
    if isinstance(exc, FileNotFoundError):
        return RuntimeError(f"Docker 소켓({DOCKER_SOCK})을 찾을 수 없어요.")
    if isinstance(exc, PermissionError):
        return RuntimeError(f"Docker 소켓({DOCKER_SOCK}) 접근 권한이 없어요.")
    return RuntimeError(f"Docker와 통신하지 못했어요: {exc}")


def _docker_get(path: str) -> tuple[int, bytes]:
    conn = _UnixConn(DOCKER_SOCK)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    except OSError as exc:
        raise _docker_error(exc) from exc
    finally:
        conn.close()


def _inspect() -> dict:
    status, body = _docker_get(f"/containers/{CONTAINER}/json")
    if status == 404:
        raise RuntimeError(f"컨테이너 '{CONTAINER}'를 찾을 수 없어요.")
    if status != 200:
        raise RuntimeError(f"컨테이너 조회 실패(HTTP {status}).")
    return json.loads(body)


def _demux(data: bytes) -> bytes:
    """tty가 꺼진 컨테이너의 로그는 8바이트 헤더가 붙은 프레임 형식이라 풀어준다."""
    out, i = [], 0
    while i + 8 <= len(data):
        size = struct.unpack(">I", data[i + 4:i + 8])[0]
        out.append(data[i + 8:i + 8 + size])
        i += 8 + size
    return b"".join(out)


def _logs(query: str, tty: bool) -> list[str]:
    status, body = _docker_get(f"/containers/{CONTAINER}/logs?stdout=1&stderr=1&{query}")
    if status != 200:
        raise RuntimeError(f"로그 조회 실패(HTTP {status}).")
    if not tty:
        body = _demux(body)
    return [ln.rstrip("\r") for ln in body.decode("utf-8", errors="replace").split("\n")]


_TS_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?Z (.*)$")


def _parse_ts_line(line: str):
    m = _TS_RE.match(line)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    frac = (m.group(2) or "0")[:6].ljust(6, "0")
    return dt.replace(microsecond=int(frac)), m.group(3)


def _lines_since(t0: datetime, tty: bool) -> list[str]:
    """t0 이후에 찍힌 로그 줄(도커 타임스탬프 접두어는 제거)."""
    raw = _logs(f"timestamps=1&since={int(t0.timestamp()) - 2}", tty)
    kept = []
    for line in raw:
        parsed = _parse_ts_line(line)
        if parsed is None:
            continue
        dt, text = parsed
        if dt >= t0:
            kept.append(text)
    return kept


def _attach_send(data: bytes) -> None:
    """컨테이너 stdin에 data를 쓴다(Docker attach를 HTTP Upgrade로 열어 raw 스트림으로 사용)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(DOCKER_SOCK)
        s.sendall(
            (f"POST /containers/{CONTAINER}/attach?stdin=1&stream=1 HTTP/1.1\r\n"
             "Host: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\nContent-Length: 0\r\n\r\n").encode()
        )
        head = s.recv(4096).decode(errors="replace")
        first = head.split("\r\n", 1)[0]
        if " 101 " not in first and " 200 " not in first:
            raise RuntimeError(f"stdin 연결 실패: {first or '응답 없음'}")
        s.sendall(data)
        time.sleep(0.2)  # 닫기 전에 전달될 시간을 준다
    except OSError as exc:
        raise _docker_error(exc) from exc
    finally:
        try:
            s.close()
        except Exception:
            pass


# --------------------------------------------------------------------------
# 명령 검증 / 실행
# --------------------------------------------------------------------------
def _run(cmd: str) -> list[str]:
    """정책 검사 없이 명령을 보내고, 그 이후 로그에서 응답을 모아 돌려준다."""
    tty = bool(_inspect().get("Config", {}).get("Tty"))
    t0 = datetime.now(timezone.utc)
    _attach_send((cmd + "\n").encode())

    lines: list[str] = []
    for attempt in range(4):
        time.sleep(0.8)
        lines = [ln for ln in _lines_since(t0, tty) if ln.strip() and ln.strip() != cmd]
        if lines:
            time.sleep(0.4)  # 여러 줄 응답의 뒷부분을 기다린다
            lines = [ln for ln in _lines_since(t0, tty) if ln.strip() and ln.strip() != cmd]
            break
    return lines


# --------------------------------------------------------------------------
# MCP 도구
# --------------------------------------------------------------------------
@mcp.tool()
def bds_status() -> dict:
    """마인크래프트(Bedrock) 서버 컨테이너의 상태(실행 여부/헬스/시작 시각)를 확인합니다."""
    info = _inspect()
    state = info.get("State", {})
    return {
        "container": CONTAINER,
        "status": state.get("Status"),
        "health": (state.get("Health") or {}).get("Status"),
        "started_at": state.get("StartedAt"),
    }


@mcp.tool()
def bds_players() -> dict:
    """마인크래프트(Bedrock) 서버의 현재 접속자 수와 이름을 조회합니다(서버 콘솔의 list 명령)."""
    lines = _run("list")
    text = "\n".join(lines)
    m = re.search(r"There are (\d+)/(\d+) players online", text)
    if not m:
        return {"raw": lines or ["(응답 없음 - 서버가 응답하지 않았어요)"]}
    idx = next(i for i, ln in enumerate(lines) if "players online" in ln)
    names_raw = [ln for ln in lines[idx + 1:] if ln.strip()]
    return {"online": int(m.group(1)), "max": int(m.group(2)), "names_raw": names_raw}


@mcp.tool()
def bds_recent_logs(lines: int = 30) -> str:
    """마인크래프트(Bedrock) 서버의 최근 로그 줄을 돌려줍니다(기본 30줄, 최대 200줄)."""
    n = max(1, min(int(lines or 30), 200))
    tty = bool(_inspect().get("Config", {}).get("Tty"))
    out = [ln for ln in _logs(f"tail={n}", tty) if ln.strip()]
    return "\n".join(out[-n:]) or "(로그 없음)"


if __name__ == "__main__":
    mcp.run(transport="stdio")
