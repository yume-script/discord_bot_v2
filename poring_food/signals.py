"""
오아시스 마을 세계 밖의 "실제 변화"를 모아서 이야기의 외부 자극으로 쓴다.

사람처럼 보이려면 이야기가 자기들끼리만 굴러가지 않고 바깥 세상 변화에 반응해야 한다.
그래서 봇이 이미 알고 있는 실제 신호를 이야기 속 사건으로 바꿔 넣는다:

| 실제 신호                        | 이야기 속 의미 (LLM에게 알려주는 대응)         |
|----------------------------------|-----------------------------------------------|
| 광주 날씨(기상청 MCP) / 오늘의 화제(실제 검색어·뉴스, news.py) | 골목 손님 발길·가게 수다·선술집 분위기 |
| 서버 상태 MCP(서버 전체 CPU/메모리/디스크 합계/온도) | 건물 전력 사용률·건물 혼잡도·지하 보급 창고 적재율·과열 |
| 카톡 브릿지 서버 상태             | 건물 통신 회선(인터넷/전화) 정상/먹통          |
| Plex/북오아시스 신규 등록 (metrics) | 신규 입고(극장 새 필름 / 도서관 새 책 상자)       |
| Plex 재생 수 / 지금 시청 중 (Tautulli) | 별빛 환영극장 상영(관람) / 지금 객석 손님           |
| 오늘 대화량                       | 주문·고객 문의 (각각 최근 7일 평균과 비교)        |
| 북오아시스 신간/장애              | 오아시스 마법도서관 서가 입고 / 서가 전산 먹통        |
| Redroid 비인가 앱 차단            | 건물 공용 와이파이 보안 사고 / 성벽 수비대 비상   |
| 요일·월말·계절                   | 월말 관리비·세금 정산, 주말 손님 러시, 휴무일    |

collect()는 가벼운 신호만(파일/DB/짧은 HTTP) 모으고, 느린 건 캐시(signals_cache.json)를 쓴다:
- 날씨/건물 설비: 매시 회차 시작 때 refresh_live()가 MCP(korea_weather, server_status)로
  실측값을 받아 넣는다 (mcp_signals.py). 기상청 날씨를 못 받으면 매시 일지의 LLM 검색 날씨
  (remember_weather)로 대신한다.
- 화제: 하루 한 번(작가 회의 때) refresh_daily_topic()으로 갱신한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from config import settings

from . import checker, mcp_signals, metrics, news, processor, world, worldcfg
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import BOOKOASIS_STATE_PATH, STATE_DIR

KST = timezone(timedelta(hours=9))
CACHE_PATH = os.path.join(STATE_DIR, "signals_cache.json")
WEATHER_MAX_AGE = timedelta(hours=3)
LIVE_MAX_AGE = timedelta(hours=2)  # 기상청 날씨/건물 설비 - 매시 갱신이니 두 번 연속 실패하면 버린다
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
    """[매시 회차 시작] MCP로 실측 날씨(기상청)와 건물 설비(서버 상태)를 받아 캐시에 넣는다."""
    live = mcp_signals.fetch()
    if not live:
        return
    cache = _load_cache()
    at = datetime.now(KST).isoformat()
    for key, value in live.items():
        if isinstance(value, dict):  # 설비 숫자 (담당 실적용)
            cache[f"live_{key}"] = {"value": value, "at": at}
        else:
            cache[f"live_{key}"] = {"text": value, "at": at}
    _save_cache(cache)
    print(f"[신호] MCP 실측값 갱신: {', '.join(live)}")


def live_value(key: str) -> dict:
    """refresh_live가 받아 둔 숫자 값 (예: "facility_numbers"). 오래됐거나 없으면 {}."""
    entry = _load_cache().get(f"live_{key}") or {}
    try:
        fresh = datetime.now(KST) - datetime.fromisoformat(entry.get("at", "")) <= LIVE_MAX_AGE
    except (TypeError, ValueError):
        return {}
    return (entry.get("value") or {}) if fresh else {}


def weather_text() -> str:
    """지금 날씨 한 줄 (기상청 실측 우선, 없으면 일지가 검색한 날씨). 없으면 빈 문자열."""
    now, cache = datetime.now(KST), _load_cache()
    return (_fresh(cache.get("live_weather", {}), now, LIVE_MAX_AGE)
            or _fresh(cache.get("weather", {}), now, WEATHER_MAX_AGE))


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
    if cache.get("topic", {}).get("date") == today or news.current():  # 실제 뉴스가 있으면 LLM 화제는 안 만든다
        return
    topic = processor.fetch_daily_topic()
    if topic:
        cache["topic"] = {"date": today, **topic}
        _save_cache(cache)


def _calendar_line(now: datetime) -> str:
    parts = [f"{now.month}월 {now.day}일 {_WEEKDAYS[now.weekday()]}요일 {now.hour}시"]
    if now.weekday() == 0:
        parts.append(f"월요일이라 {worldcfg.BOOK} 휴관")
    elif now.weekday() == 4:
        parts.append("금요일이라 장 서기 전날, 손님 기대")
    elif now.weekday() >= 5:
        parts.append("주말이라 골목에 손님이 많은 날")
    last_day = (now.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if last_day.day - now.day <= 2:
        parts.append("월말 마감 직전")
    elif now.day == 25:
        parts.append("품삯 받는 날")
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
        return f"{worldcfg.BOOK} 서가 전산(북오아시스)이 먹통 (사장 애순이 담당)"
    today = datetime.now(KST).strftime("%Y-%m-%d")
    today_new = [a for a in data.get("recent_arrivals", []) if str(a.get("added", "")).startswith(today)]
    if today_new:
        return f"{worldcfg.BOOK} 서가에 오늘 새 책 상자 {len(today_new)}건 입고 (사장 애순이 담당)"
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

    # 오늘 세상의 화제: 실제 검색어/뉴스 제목(news.py). 못 받았으면 예전처럼 LLM이 고른 화제 하나
    topic_news = news.topic_line(now)
    topic = cache.get("topic", {})
    if topic_news:
        signals["topic"] = topic_news
    elif topic.get("date") == now.strftime("%Y-%m-%d") and topic.get("topic_title"):
        signals["topic"] = f"{topic['topic_title']} - {topic.get('topic_summary', '')}"

    if include_factory:
        try:
            msg, ok = checker.check_poring_factory_status()
            signals["factory"] = "통신석 중계탑 정상" if ok else f"통신석 중계탑 이상: {msg}"
        except Exception:  # noqa: BLE001
            pass

    facility = _fresh(cache.get("live_facility", {}), now, LIVE_MAX_AGE)
    if facility:
        signals["facility"] = facility

    production = metrics.describe()
    if production:
        signals["production"] = production

    happened = world.today_lines()
    if happened:
        signals["world"] = " / ".join(happened[-5:])

    book = _bookoasis_line()
    if book:
        signals["bookoasis"] = book

    blocked = _redroid_today(now)
    if blocked:
        signals["security"] = f"오늘 성문 결계에 수상한 저주 부적을 들이려는 시도 {blocked}건 - 성벽 수비대 경비 결계가 자동 차단"

    return signals


# 누가 어떤 신호를 아는가 (신호 키 -> 알 수 있는 회사들).
# - 건물 설비(통신 회선/전력/창고/보안)는 같은 건물을 쓰는 두 가게와 성벽 수비대(오아시스 마을)가 다 안다.
#   가족/친구(FRIENDS)처럼 건물 밖 사람은 모른다.
# - 입고/상영 현황은 두 가게 안의 일이라 두 가게 사람만, 도서관 서가 소식은 도서관 사람만 안다.
BUILDING_COMPANIES = (worldcfg.BOOK, worldcfg.CINEMA, worldcfg.STREET)
KNOWN_BY = {
    "factory": BUILDING_COMPANIES,
    "facility": BUILDING_COMPANIES,
    "security": BUILDING_COMPANIES,
    "production": worldcfg.OFFICES,
    "bookoasis": (worldcfg.BOOK,),
}
INTERNAL_KEYS = tuple(KNOWN_BY)
# 예전 이름 호환 - 건물 안 사정을 아는 "본진"(대표로 애순이의 도서관)
HOME_COMPANY = worldcfg.BOOK


def knows(company: str, key: str) -> bool:
    """그 회사 사람이 이 신호를 알 수 있는지 (목록에 없는 키는 누구나 안다)."""
    allowed = KNOWN_BY.get(key)
    return allowed is None or company in allowed


def for_company(signals: dict, company: str) -> dict:
    """그 회사 사람이 알 수 있는 신호만 남긴다. 회사/마을 사건도 그 회사 것과 마을 것만."""
    out = dict(signals)
    for k in INTERNAL_KEYS:
        if not knows(company, k):
            out.pop(k, None)
    happened = world.today_lines(company)
    if happened:
        out["world"] = " / ".join(happened[-5:])
    else:
        out.pop("world", None)
    return out


def format_block(signals: dict) -> str:
    """프롬프트에 넣을 "바깥 세상 변화" 블록."""
    labels = {
        "calendar": "날짜/시간", "weather": "날씨(전투 날씨)", "topic": "떠돌이 상인이 전한 왕국 소문",
        "factory": "통신석 중계탑", "facility": "성벽과 결계(결계 부하/보급 창고)", "world": "오늘 기관/마을에서 생긴 일",
        "production": "입고/환영극 현황", "bookoasis": "도서관 서가 소식", "security": "성문 경비",
    }
    lines = [f"- {labels[k]}: {v}" for k, v in signals.items() if k in labels and v]
    if not lines:
        return ""
    return "[세상 변화 - 실제로 일어난 일이다. 인물들이 자연스럽게 반응하게 하되, 수치는 그대로 쓰고 지어내지 마라]\n" + "\n".join(lines)
