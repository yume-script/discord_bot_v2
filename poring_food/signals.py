"""
포링푸드 세계 밖의 "실제 변화"를 모아서 이야기의 외부 자극으로 쓴다.

사람처럼 보이려면 이야기가 자기들끼리만 굴러가지 않고 바깥 세상 변화에 반응해야 한다.
그래서 봇이 이미 알고 있는 실제 신호를 이야기 속 사건으로 바꿔 넣는다:

| 실제 신호                        | 이야기 속 의미 (LLM에게 알려주는 대응)         |
|----------------------------------|-----------------------------------------------|
| 광주 날씨(기상청 MCP) / 오늘의 화제 | 출근길·점심 수다·회식 분위기                    |
| 서버 상태 MCP(CPU/메모리/디스크/온도) | 공장 설비 가동률·작업장 혼잡도·창고 적재율·과열 |
| 카톡 브릿지 서버 상태             | 포링푸드 공장 라인 가동/정지                    |
| Plex/북오아시스 신규 등록 (metrics) | 생산량(신규 입고)                              |
| Plex 재생 수 / 지금 시청 중 (Tautulli) | 출하(판매) / 매장 손님                        |
| 오늘 대화량                       | 주문·고객 문의 (각각 최근 7일 평균과 비교)        |
| 북오아시스 신간/장애              | 사내 자료실 입고 / 전산 먹통 (애순이 겸직)      |
| Redroid 비인가 앱 차단            | 사내 보안 사고 / 보안팀 비상                     |
| 요일·월말·계절                   | 월말 마감 압박, 토요 특근, 금요일 퇴근 분위기    |

collect()는 가벼운 신호만(파일/DB/짧은 HTTP) 모으고, 느린 건 캐시(signals_cache.json)를 쓴다:
- 날씨/공장 설비: 매시 회차 시작 때 refresh_live()가 MCP(korea_weather, server_status)로
  실측값을 받아 넣는다 (mcp_signals.py). 기상청 날씨를 못 받으면 매시 일지의 LLM 검색 날씨
  (remember_weather)로 대신한다.
- 화제: 하루 한 번(작가 회의 때) refresh_daily_topic()으로 갱신한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from config import settings

from . import checker, mcp_signals, metrics, processor
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import BOOKOASIS_STATE_PATH, STATE_DIR

KST = timezone(timedelta(hours=9))
CACHE_PATH = os.path.join(STATE_DIR, "signals_cache.json")
WEATHER_MAX_AGE = timedelta(hours=3)
LIVE_MAX_AGE = timedelta(hours=2)  # 기상청 날씨/공장 설비 - 매시 갱신이니 두 번 연속 실패하면 버린다
_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _load_cache() -> dict:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CACHE_PATH)
    except OSError as e:
        print(f"[경고] 외부 신호 캐시 저장 실패: {e}")


def remember_weather(weather: str) -> None:
    """매시 일지가 조회한 날씨를 장면/작가 회의가 다시 쓰도록 저장 (LLM 검색 호출 절약)."""
    if not weather or "가져올 수 없음" in weather:
        return
    cache = _load_cache()
    cache["weather"] = {"text": weather, "at": datetime.now(KST).isoformat()}
    _save_cache(cache)


def refresh_live() -> None:
    """[매시 회차 시작] MCP로 실측 날씨(기상청)와 공장 설비(서버 상태)를 받아 캐시에 넣는다."""
    live = mcp_signals.fetch()
    if not live:
        return
    cache = _load_cache()
    at = datetime.now(KST).isoformat()
    for key, text in live.items():
        cache[f"live_{key}"] = {"text": text, "at": at}
    _save_cache(cache)
    print(f"[신호] MCP 실측값 갱신: {', '.join(live)}")


def _fresh(entry: dict, now: datetime, max_age: timedelta) -> str:
    try:
        if entry and now - datetime.fromisoformat(entry["at"]) <= max_age:
            return entry["text"]
    except (KeyError, ValueError, TypeError):
        pass
    return ""


def live_weather() -> str:
    """기상청 실측 날씨(최근 회차에 받은 것). 없으면 빈 문자열 - 매시 일지가 LLM 검색 대신 쓴다."""
    return _fresh(_load_cache().get("live_weather", {}), datetime.now(KST), LIVE_MAX_AGE)


def refresh_daily_topic() -> None:
    """오늘의 화제(뉴스/스포츠/영화 등)를 하루 한 번 갱신한다."""
    cache = _load_cache()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if cache.get("topic", {}).get("date") == today:
        return
    topic = processor.fetch_daily_topic()
    if topic:
        cache["topic"] = {"date": today, **topic}
        _save_cache(cache)


def _calendar_line(now: datetime) -> str:
    parts = [f"{now.month}월 {now.day}일 {_WEEKDAYS[now.weekday()]}요일 {now.hour}시"]
    if now.weekday() == 4:
        parts.append("금요일이라 퇴근 분위기")
    elif now.weekday() == 5:
        parts.append("토요일 특근")
    elif now.weekday() == 6:
        parts.append("일요일 휴무")
    last_day = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if last_day.day - now.day <= 2:
        parts.append("월말 마감 직전")
    elif now.day == 25:
        parts.append("월급날")
    season = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
              6: "여름", 7: "여름", 8: "여름"}.get(now.month, "가을")
    parts.append(season)
    return ", ".join(parts)


def _redroid_today(now: datetime) -> int:
    """오늘 Redroid 감시가 기록한 변동(비인가 앱 자동 삭제 포함) 건수."""
    path = settings.REDROID_EVENTS_PATH
    if not path.exists():
        return 0
    today = now.strftime("%Y-%m-%d")
    count = 0
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if today in line and '"auto_removed": [{' in line:
                    count += 1
    except OSError:
        return 0
    return count


def _bookoasis_line() -> str:
    try:
        with open(BOOKOASIS_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    if data.get("last_ok") is False:
        return "사내 자료실(북오아시스) 전산이 먹통 (애순이 겸직 업무)"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    today_new = [a for a in data.get("recent_arrivals", []) if str(a.get("added", "")).startswith(today)]
    if today_new:
        return f"사내 자료실(북오아시스)에 오늘 신간 {len(today_new)}건 입고 (애순이 겸직 업무)"
    return ""


def collect(include_factory: bool = True) -> dict:
    """지금 시점의 외부 신호를 모은다. 각 항목은 실패해도 빠질 뿐 예외를 올리지 않는다."""
    now = datetime.now(KST)
    cache = _load_cache()
    signals: dict = {"calendar": _calendar_line(now)}

    weather = (_fresh(cache.get("live_weather", {}), now, LIVE_MAX_AGE)
               or _fresh(cache.get("weather", {}), now, WEATHER_MAX_AGE))
    if weather:
        signals["weather"] = weather

    topic = cache.get("topic", {})
    if topic.get("date") == now.strftime("%Y-%m-%d") and topic.get("topic_title"):
        signals["topic"] = f"{topic['topic_title']} - {topic.get('topic_summary', '')}"

    if include_factory:
        try:
            msg, ok = checker.check_poring_factory_status()
            signals["factory"] = "공장 라인 정상 가동" if ok else f"공장 라인 이상: {msg}"
        except Exception:  # noqa: BLE001
            pass

    facility = _fresh(cache.get("live_facility", {}), now, LIVE_MAX_AGE)
    if facility:
        signals["facility"] = facility

    production = metrics.describe()
    if production:
        signals["production"] = production

    book = _bookoasis_line()
    if book:
        signals["bookoasis"] = book

    blocked = _redroid_today(now)
    if blocked:
        signals["security"] = f"오늘 사내 전산망에 수상한 프로그램 설치 시도 {blocked}건 - 보안 시스템이 자동 차단"

    return signals


def format_block(signals: dict) -> str:
    """프롬프트에 넣을 "바깥 세상 변화" 블록."""
    labels = {
        "calendar": "날짜/시간", "weather": "날씨(광주)", "topic": "오늘 세상의 화제",
        "factory": "공장 상태", "facility": "공장 설비", "production": "생산/판매 현황", "bookoasis": "사내 자료실",
        "security": "사내 보안",
    }
    lines = [f"- {labels[k]}: {v}" for k, v in signals.items() if k in labels and v]
    if not lines:
        return ""
    return "[바깥 세상 변화 - 실제로 일어난 일이다. 인물들이 자연스럽게 반응하게 하되, 수치는 그대로 쓰고 지어내지 마라]\n" + "\n".join(lines)
