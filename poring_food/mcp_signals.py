"""
봇이 연결해 둔 MCP 서버에서 "실제 수치"를 가져와 포링푸드 이야기의 외부 신호로 바꾼다.

| MCP 서버 / 도구                   | 이야기 속 의미                                       |
|-----------------------------------|-----------------------------------------------------|
| korea_weather / get_forecast      | 광주 날씨(기상청 초단기예보) - 출근길·현장·점심 수다    |
| server_status / get_system_status | 공장 설비: CPU=설비 가동률, 메모리=작업장 혼잡도,       |
|                                   | 디스크=창고 적재율, 온도=설비 온도, 가동시간=연속 가동  |
| server_status / get_service_status| 핵심 라인(서비스) 정지                               |

- 매시 회차 시작 때 refresh()가 한 번 조회해서 signals_cache.json에 넣고, 장면/작가 회의/일지가
  그 캐시를 읽는다 (MCP 서버 기동이 느려서 장면마다 다시 부르지 않는다).
- 서버마다 세션 하나로 필요한 도구를 다 부른다 (stdio 서버를 호출마다 새로 띄우지 않게).
- 실패한 서버는 그냥 빠진다 - 이야기는 신호 없이도 굴러가야 한다.
- 평소 수치는 한 줄로만 주고, 임계값을 넘은 것만 "사건"으로 표시해 이야기가 수치 얘기로
  도배되지 않게 한다.
"""
from __future__ import annotations

import asyncio
import os
import re

from . import runtime
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .bookoasis import _parse_tool_result, _root_cause

WEATHER_SERVER = os.getenv("PORING_WEATHER_MCP_SERVER", "korea_weather")
FACILITY_SERVER = os.getenv("PORING_FACILITY_MCP_SERVER", "server_status")
# 광주광역시청 좌표 (기상청 격자 nx=58, ny=74)
WEATHER_LAT = float(os.getenv("PORING_WEATHER_LAT", "35.1595"))
WEATHER_LON = float(os.getenv("PORING_WEATHER_LON", "126.8526"))
# uvx로 뜨는 korea_weather는 첫 기동에 패키지 해석이 들어가서 넉넉하게 잡는다
TIMEOUT_SEC = int(os.getenv("PORING_MCP_SIGNAL_TIMEOUT_SEC", "60"))


# ---------------------------------------------------------------- 날씨 (korea_weather)

def _parse_forecast(text: str) -> dict:
    """get_forecast의 "기온: 18°C\\n하늘상태: 맑음..." 텍스트를 dict로."""
    data = {}
    for line in (text or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and value.strip():
            data[key.strip()] = value.strip()
    return data


def _number(value) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    return float(m.group()) if m else None


def describe_weather(fc: dict) -> str:
    """기상청 예보 dict -> 이야기용 한 줄. 필요한 값이 없으면 빈 문자열."""
    temp = _number(fc.get("기온"))
    if temp is None:
        return ""
    sky = fc.get("하늘상태", "")
    pty = fc.get("강수형태", "없음")
    parts = [f"기온 {temp:g}°C", sky or None]
    if pty and pty != "없음":
        rain = fc.get("1시간 강수량", "")
        parts.append(f"{pty} ({rain})" if rain and rain != "강수없음" else pty)
    humidity = _number(fc.get("습도"))
    wind = _number(fc.get("풍속"))
    if humidity is not None:
        parts.append(f"습도 {humidity:g}%")
    if wind is not None:
        parts.append(f"풍속 {wind:g}m/s")

    events = []
    if temp >= 33:
        events.append("폭염 - 현장 더위 비상")
    elif temp <= -5:
        events.append("한파 - 출근길 꽁꽁")
    rain_mm = _number(fc.get("1시간 강수량"))
    if rain_mm is not None and rain_mm >= 10:
        events.append("폭우 - 출근길/배송 차질")
    elif "눈" in pty:
        events.append("눈 - 출근길 미끄러움")
    if wind is not None and wind >= 9:
        events.append("강풍")
    line = ", ".join(p for p in parts if p)
    if events:
        line += " → " + ", ".join(events)
    return line + " (기상청 실측 예보)"


async def _weather_async() -> str:
    from ai.mcp_manager import server_session
    async with server_session(WEATHER_SERVER) as session:
        res = await session.call_tool("get_forecast", {"latitude": WEATHER_LAT, "longitude": WEATHER_LON})
    if getattr(res, "isError", False):
        raise RuntimeError(_result_text(res) or "get_forecast 오류")
    return describe_weather(_parse_forecast(_result_text(res)))


def _result_text(res) -> str:
    structured = getattr(res, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]
    return "\n".join(getattr(b, "text", "") or "" for b in getattr(res, "content", None) or [])


# ---------------------------------------------------------------- 공장 설비 (server_status)

def _get(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _uptime_days(uptime) -> float | None:
    """"12 days, 3:04:05" / "3:04:05" -> 일 수."""
    if not uptime:
        return None
    m = re.match(r"(?:(\d+) days?, )?(\d+):(\d+):(\d+)", str(uptime))
    if not m:
        return None
    days, h, mi, _ = (int(x or 0) for x in m.groups())
    return days + h / 24 + mi / 1440


def describe_facility(status: dict, services: dict | None = None) -> str:
    """get_system_status(+get_service_status) 결과 -> 공장 설비 한 줄. 값이 없으면 빈 문자열."""
    if not isinstance(status, dict) or not status:
        return ""
    cpu = _get(status, "cpu", "container_percent_of_machine")
    if cpu is None:
        cpu = _get(status, "cpu", "host_percent")
    mem = _get(status, "memory", "used_percent")
    disks = [d.get("used_percent") for d in status.get("disks") or [] if isinstance(d, dict)]
    disks = [d for d in disks if isinstance(d, (int, float))]
    disk = max(disks) if disks else None
    temp = _get(status, "temperature_c", "max")
    days = _uptime_days(status.get("uptime"))

    parts, events = [], []
    if isinstance(cpu, (int, float)):
        parts.append(f"설비 가동률 {cpu:g}%")
        if cpu >= 80:
            events.append("설비 과부하 - 라인이 숨가쁘게 돌아감")
    if isinstance(mem, (int, float)):
        parts.append(f"작업장 혼잡도 {mem:g}%")
        if mem >= 90:
            events.append("작업장이 꽉 차서 발 디딜 틈 없음")
    if disk is not None:
        parts.append(f"창고 적재율 {disk:g}%")
        if disk >= 90:
            events.append("창고 포화 직전 - 재고 정리 비상")
        elif disk >= 80:
            events.append("창고가 꽤 찼음")
    if isinstance(temp, (int, float)):
        parts.append(f"설비 온도 {temp:g}°C")
        if temp >= 80:
            events.append("설비 과열 경보")
    if days is not None:
        parts.append(f"연속 가동 {int(days)}일")
        if days < 1:
            events.append("최근 설비 재가동(정비/정전 후 복구)")

    down = [name for name, s in (services or {}).items()
            if isinstance(s, dict) and s.get("state") in ("failed", "inactive")]
    if down:
        events.append(f"멈춘 라인 {len(down)}개")

    if not parts:
        return ""
    line = ", ".join(parts)
    return line + (" → " + ", ".join(events) if events else " (정상 범위)")


async def _facility_async() -> str:
    from ai.mcp_manager import server_session
    async with server_session(FACILITY_SERVER) as session:
        status = _parse_tool_result(await session.call_tool("get_system_status", {}))
        services = {}
        try:
            services = _parse_tool_result(await session.call_tool("get_service_status", {}))
        except Exception as e:  # noqa: BLE001 - 서비스 상태는 덤
            print(f"[경고] 서비스 상태 조회 실패: {_root_cause(e)!r}")
    return describe_facility(status, services)


# ---------------------------------------------------------------- 수집

async def _gather_async() -> dict:
    names = ("weather", "facility")
    results = await asyncio.gather(_weather_async(), _facility_async(), return_exceptions=True)
    out = {}
    for name, res in zip(names, results):
        if isinstance(res, BaseException):
            print(f"[경고] 외부 신호({name}) MCP 조회 실패: {_root_cause(res)!r}")
        elif res:
            out[name] = res
    return out


def fetch() -> dict:
    """[스레드에서 호출] {"weather": "...", "facility": "..."} - 실패한 항목은 빠진다."""
    if not runtime.is_bound():
        return {}
    try:
        return runtime.run_on_bot_loop(_gather_async(), timeout=TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 외부 신호 MCP 조회 실패: {_root_cause(e)!r}")
        return {}
