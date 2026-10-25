"""
오아시스 마을 "입고/상영/문의" 지표 - 실제 서비스 수치를 도서관·극장·건물 실적으로 바꾼다.
(코드 안 이름은 예전 그대로 생산=신규 입고, 판매=상영/출하, 주문=손님 문의)

| 이야기 지표          | 실제 데이터                                   | 가져오는 곳                          |
|---------------------|----------------------------------------------|-------------------------------------|
| 생산량(신규 입고)     | 오늘 Plex 신규 등록 + 북오아시스 신규 권 수      | plex get_recently_added,            |
|                     | (서재별 전체 권 수 - 어제 마지막 전체 권 수)     | bookoasis get_library_stats         |
|  └ 담당 라인         | books=도서관 일반/웹툰 서가(애순이), books_adult=금서고, plex=극장 필름 |                                     |
| 판매량(별빛 환영극장 상영) | 오늘 Plex 재생 수, 지금 시청 중인 수(객석 손님)  | tautulli plays_by_date / activity   |
| 주문·고객 문의        | 오늘 사람이 봇에게 보낸 메시지 수 - 최수연 실적은 | 대화 로그 DB (예전 "생산량")         |
|                     | 가상 손님 문의(kpi.py desk), 이건 VIP 문의로만 가산 |                                     |
| 연산 처리 / 관리비    | 오늘 LLM 요청 수 / 토큰                       | llm_usage get_llm_usage (LiteLLM     |
|                     |                                              | 프록시 전체, 못 받으면 봇 직접 집계)  |

[변경] 예전엔 생산량=사람 메시지 수, 판매량=봇 응답 수였고 목표가 1000건 고정이라 늘 "한산함"
이었다. 이제 기준은 각 지표의 "최근 7일 하루 평균"이다 - 실제로 평소보다 바쁜지가 이야기에 나온다.

- 매시 회차 시작 때 refresh()가 MCP 서버 셋을 조회해서 metrics.json에 저장하고, 일지/장면은
  그 값을 읽는다. 실패한 서버는 직전 값(같은 날짜일 때만)을 그대로 쓴다.
- 이야기에는 개수만 쓴다 (제목을 넘기지 않으니 adult 서재/라이브러리 제목이 새지 않는다).
  북오아시스는 이야기용 서재(BOOKOASIS_STORY_DB_TYPES)만 센다.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from . import runtime
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .bookoasis import _parse_tool_result, _root_cause
from .config import (
    BOOKOASIS_ADULT_DB_TYPES, BOOKOASIS_ENABLED, BOOKOASIS_MCP_SERVER, BOOKOASIS_STORY_DB_TYPES, BOOKOASIS_TIMEOUT_SEC,
    DISCORD_BOT_V2_DB_PATH, STATE_DIR,
)

KST = timezone(timedelta(hours=9))
METRICS_PATH = os.path.join(STATE_DIR, "metrics.json")
PLEX_SERVER = os.getenv("PORING_PLEX_MCP_SERVER", "plex")
TAUTULLI_SERVER = os.getenv("PORING_TAUTULLI_MCP_SERVER", "tautulli")
LLM_USAGE_SERVER = os.getenv("PORING_LLM_USAGE_MCP_SERVER", "llm_usage")
PLEX_FETCH_LIMIT = int(os.getenv("PORING_PLEX_FETCH_LIMIT", "50"))
# 최근 7일 기록이 아직 없을 때 쓰는 하루 생산 목표
DEFAULT_PRODUCTION_TARGET = float(os.getenv("PORING_PRODUCTION_DEFAULT_TARGET", "5"))
AVG_DAYS = 7


# ---------------------------------------------------------------- 저장

def _load() -> dict:
    try:
        with open(METRICS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        tmp = METRICS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, METRICS_PATH)
    except OSError as e:
        print(f"[경고] 생산 지표 저장 실패: {e}")


def _past_days(today: str) -> list[str]:
    d = datetime.strptime(today, "%Y-%m-%d")
    return [(d - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, AVG_DAYS + 1)]


# ---------------------------------------------------------------- 생산 (Plex + 북오아시스)
# Plex는 최근 등록 목록을 날짜별로 센다. 북오아시스는 서재별 전체 권 수(get_library_stats)를
# 매시 기록해 두고 "지금 - 어제 마지막 값"으로 센다 (시리즈 수가 아니라 권 수).

def daily_counts(dates: list[str], fetched_full: bool) -> tuple[dict, str]:
    """
    날짜 목록(최신순으로 받아온 항목들의 등록일) -> ({날짜: 개수}, 집계가 완전한 가장 오래된 날짜).
    한도(limit)만큼 꽉 차게 받아왔으면 가장 오래된 날짜는 일부만 들어왔을 수 있어서 그날은 빼고,
    덜 받아왔으면(=전부 받아왔으면) 모든 날짜가 완전하다.
    """
    counts: dict[str, int] = {}
    for d in dates:
        counts[d] = counts.get(d, 0) + 1
    if not dates:
        return counts, "0000-00-00"
    oldest = min(dates)  # 전부 오늘 것으로 꽉 찼으면 complete_from이 내일이 된다 (=지난 날짜는 하나도 모름)
    if fetched_full:
        # oldest 날짜는 잘렸을 수 있다 -> 그 다음 날부터 완전
        nxt = (datetime.strptime(oldest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        return counts, nxt
    return counts, "0000-00-00"


async def _plex_async() -> tuple[dict, str]:
    from ai.mcp_manager import server_session
    async with server_session(PLEX_SERVER) as session:
        res = await session.call_tool("get_recently_added", {"limit": PLEX_FETCH_LIMIT})
    items = _parse_tool_result(res).get("recentlyAdded", []) or []
    dates = []
    for item in items:
        try:
            ts = int(item.get("addedAt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if ts:
            dates.append(datetime.fromtimestamp(ts, KST).strftime("%Y-%m-%d"))
    return daily_counts(dates, fetched_full=len(items) >= PLEX_FETCH_LIMIT)


async def _books_async() -> dict:
    """서재별 전체 도서(권) 수 {db_type: total_book_count}. 시리즈 수가 아니라 권 수다."""
    from ai.mcp_manager import server_session
    totals: dict[str, int] = {}
    async with server_session(BOOKOASIS_MCP_SERVER) as session:
        for db_type in dict.fromkeys(BOOKOASIS_STORY_DB_TYPES + BOOKOASIS_ADULT_DB_TYPES):
            res = await session.call_tool("get_library_stats", {"db_type": db_type})
            if getattr(res, "isError", False):
                continue
            total = _parse_tool_result(res).get("total_book_count")
            if isinstance(total, int):
                totals[db_type] = total
    if not totals:
        raise RuntimeError("get_library_stats에서 total_book_count를 못 받음")
    return totals


def _books_today(totals: dict, book_totals: dict, today: str) -> int:
    """
    오늘 입고된 권 수 = 지금 전체 권 수 - 기준값. 기준값은 어제(이전 기록일)의 마지막 전체 권 수이고,
    기록이 없는 첫날엔 오늘 처음 잰 값이다 (그날은 처음 잰 뒤로 들어온 것만 센다).
    삭제로 줄어든 서재는 0으로 본다.
    """
    prev_days = sorted(d for d in book_totals if not d.startswith("_") and d < today)
    first = book_totals.get("_first_" + today) or totals
    base = book_totals[prev_days[-1]] if prev_days else first
    # 어제 기록에 없는 서재(새로 세기 시작한 서재)는 오늘 처음 잰 값을 기준으로
    return sum(max(0, n - base.get(db, first.get(db, n))) for db, n in totals.items())


# ---------------------------------------------------------------- 판매 (Tautulli)

_PLAY_LINE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}):\s*(\d+)", re.M)
_STREAMS = re.compile(r"(\d+)\s+active stream")


def _tool_text(res) -> str:
    structured = getattr(res, "structuredContent", None)
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"]
    return "\n".join(getattr(b, "text", "") or "" for b in getattr(res, "content", None) or [])


def parse_plays(text: str) -> dict:
    """tautulli_plays_by_date 텍스트 -> {날짜: 재생 수}."""
    return {d: int(n) for d, n in _PLAY_LINE.findall(text or "")}


def parse_streams(text: str) -> int:
    """tautulli_activity 텍스트 -> 지금 시청 중인 스트림 수 ("No active streams"면 0)."""
    m = _STREAMS.search(text or "")
    return int(m.group(1)) if m else 0


async def _tautulli_async() -> dict:
    from ai.mcp_manager import server_session
    async with server_session(TAUTULLI_SERVER) as session:
        plays = parse_plays(_tool_text(await session.call_tool("tautulli_plays_by_date", {"days": AVG_DAYS + 1})))
        streams = parse_streams(_tool_text(await session.call_tool("tautulli_activity", {})))
    return {"plays": plays, "streams": streams}


# ---------------------------------------------------------------- LLM 사용량 (LiteLLM 프록시 / 봇 집계)

async def _llm_async() -> dict:
    from ai.mcp_manager import server_session
    async with server_session(LLM_USAGE_SERVER) as session:
        return _parse_tool_result(await session.call_tool("get_llm_usage", {"days": AVG_DAYS + 1}))


def _present_avg(by_day: dict, today: str) -> float | None:
    """지난 7일 중 기록이 있는 날만의 하루 평균 (집계를 시작하기 전 날을 0으로 치지 않게)."""
    vals = [by_day[d] for d in _past_days(today) if d in by_day]
    return round(sum(vals) / len(vals), 1) if vals else None


def llm_summary(proxy: dict | None, bot: dict, today: str) -> dict | None:
    """
    프록시 기록(있으면, 같은 LiteLLM을 쓰는 다른 서비스까지 전체)이나 봇 집계 -> 오늘 지표
    {"today", "avg", "tokens_today", "tokens_avg", "failures_today", "bot_today", "source"}. 둘 다 없으면 None.
    실패 수는 봇 집계에만 있다 (프록시 새 API에 있으면 그걸 쓴다).
    """
    days = (proxy or {}).get("days") if isinstance((proxy or {}).get("days"), dict) else None
    source = "LiteLLM 전체"
    if not days:
        days, source = bot, "봇 집계"
    if not days:
        return None
    req = {d: int(v.get("requests", 0)) for d, v in days.items() if isinstance(v, dict)}
    tok = {d: int(v.get("tokens", 0)) for d, v in days.items() if isinstance(v, dict)}
    t = days.get(today) or {}
    failures = t.get("failures") if "failures" in t else (bot.get(today) or {}).get("failures", 0)
    return {"today": req.get(today, 0), "avg": _present_avg(req, today),
            "tokens_today": tok.get(today, 0), "tokens_avg": _present_avg(tok, today),
            "failures_today": int(failures or 0), "bot_today": int((bot.get(today) or {}).get("requests", 0)),
            "source": source}


# ---------------------------------------------------------------- 주문 (대화 로그 DB)

def _orders_by_day(today: str) -> dict:
    """오늘과 지난 7일의 날짜별 사람 메시지 수 (direction='in')."""
    if not os.path.exists(DISCORD_BOT_V2_DB_PATH):
        print(f"[경고] discord_bot_v2 DB를 못 찾음: {DISCORD_BOT_V2_DB_PATH}")
        return {}
    since = _past_days(today)[-1]
    try:
        conn = sqlite3.connect(f"file:{DISCORD_BOT_V2_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT substr(ts, 1, 10) AS d, COUNT(*) FROM messages "
                "WHERE direction = 'in' AND ts >= ? GROUP BY d",
                (since,),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 주문(대화량) 집계 실패: {e}")
        return {}
    return {d: n for d, n in rows}


# ---------------------------------------------------------------- 갱신

async def _gather_async() -> dict:
    jobs = {"plex": _plex_async(), "tautulli": _tautulli_async(), "llm": _llm_async()}
    if BOOKOASIS_ENABLED:
        jobs["books"] = _books_async()
    results = await asyncio.gather(*jobs.values(), return_exceptions=True)
    out = {}
    for name, res in zip(jobs, results):
        if isinstance(res, BaseException):
            print(f"[경고] 생산 지표({name}) 조회 실패: {_root_cause(res)!r}")
        else:
            out[name] = res
    return out


def _avg(by_day: dict, today: str, complete_from: str = "0000-00-00") -> float | None:
    """지난 7일 하루 평균. complete_from 이전 날짜는 집계가 잘렸을 수 있어 뺀다."""
    days = [d for d in _past_days(today) if d >= complete_from]
    if not days:
        return None
    return round(sum(by_day.get(d, 0) for d in days) / len(days), 1)


def refresh() -> None:
    """[스레드에서 호출, 매시 회차 시작] 지표를 새로 집계해서 metrics.json에 저장한다."""
    today = datetime.now(KST).strftime("%Y-%m-%d")
    prev = _load()
    same_day = prev.get("date") == today
    data = {"date": today, "at": datetime.now(KST).isoformat()}
    # 부문별 날짜별 생산 기록 {"plex": {날짜: 개수}, "books": {...}} - 받아온 목록만으로 지난 7일을
    # 다 못 덮는 날(신간이 한꺼번에 들어와 한도가 오늘 것으로 꽉 찬 날)의 평균을 여기서 낸다.
    history = prev.get("production_history", {})
    if not all(isinstance(v, dict) for v in history.values()):
        history = {}  # 예전 형식({날짜: 합계})은 부문을 몰라 버린다

    fetched = {}
    if runtime.is_bound():
        timeout = max(BOOKOASIS_TIMEOUT_SEC, 60) if BOOKOASIS_ENABLED else 60
        try:
            fetched = runtime.run_on_bot_loop(_gather_async(), timeout=timeout)
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 생산 지표 조회 실패: {_root_cause(e)!r}")

    # 북오아시스: 서재별 전체 권 수 기록 {날짜: {db_type: 그날 마지막 전체 권 수}}
    past = _past_days(today)
    book_totals = {d: v for d, v in prev.get("book_totals", {}).items()
                   if d.removeprefix("_first_") >= past[-1]}
    if "books" in fetched:
        totals = fetched["books"]
        first = book_totals.setdefault("_first_" + today, {})
        for db, n in totals.items():
            first.setdefault(db, n)
        adult = set(BOOKOASIS_ADULT_DB_TYPES)
        general = {db: n for db, n in totals.items() if db not in adult}
        adult_totals = {db: n for db, n in totals.items() if db in adult}
        book_totals[today] = totals
        # 아래 공통 처리에 맞춰 {날짜: 개수} 형태로 - 지난 날짜 값은 history에 쌓아 둔 걸 쓴다
        del fetched["books"]
        if general:
            fetched["books"] = ({today: _books_today(general, book_totals, today)}, today)
        if adult_totals:
            fetched["books_adult"] = ({today: _books_today(adult_totals, book_totals, today)}, today)
    data["book_totals"] = book_totals

    # 생산: Plex + 북오아시스. 못 받은 쪽은 오늘 직전 값 유지.
    parts = {}
    for key in PART_KEYS:
        if key in fetched:
            counts, complete_from = fetched[key]
            # 받아온 게 전부 오늘 것이면 한도에 걸린 것 - 오늘 실제 개수는 이보다 많을 수 있다
            saturated = complete_from > today
            part_hist = history.setdefault(key, {})
            for d in past:  # 받아온 목록이 완전하게 덮는 지난 날짜는 기록을 실측값으로 맞춘다
                if d >= complete_from:
                    part_hist[d] = counts.get(d, 0)
            part_hist[today] = counts.get(today, 0)
            avg = _avg(counts, today, complete_from)
            if avg is None:
                recorded = [part_hist[d] for d in past if d in part_hist]
                avg = round(sum(recorded) / len(recorded), 1) if recorded else None
            parts[key] = {"today": counts.get(today, 0), "avg": avg, "saturated": saturated}
        elif same_day and key in prev.get("production", {}).get("parts", {}):
            parts[key] = prev["production"]["parts"][key]
    if parts:
        data["production"] = {"today": sum(p["today"] for p in parts.values()), "parts": parts}
    elif same_day and "production" in prev:
        data["production"] = prev["production"]
    data["production_history"] = {
        k: {d: n for d, n in v.items() if d >= past[-1]} for k, v in history.items()
    }

    # 판매: Tautulli (꺼져 있으면 Plex에 바로 - plex_api)
    if "tautulli" not in fetched:
        from . import plex_api
        plays = plex_api.plays_by_date(AVG_DAYS + 1) if plex_api.enabled() else None
        live = plex_api.sessions() if plays is not None else None
        if plays is not None and live is not None:
            fetched["tautulli"] = {"plays": plays, "streams": sum(1 for s in live if s.get("state") != "paused")}
            print("[통계] Tautulli 대신 Plex에서 바로 재생 수를 받음")
    if "tautulli" in fetched:
        t = fetched["tautulli"]
        data["sales"] = {"today": t["plays"].get(today, 0), "avg": _avg(t["plays"], today),
                         "streams": t["streams"]}
    elif same_day and "sales" in prev:
        data["sales"] = prev["sales"]

    # 주문: 대화 로그 DB
    orders = _orders_by_day(today)
    if orders or os.path.exists(DISCORD_BOT_V2_DB_PATH):
        data["orders"] = {"today": orders.get(today, 0), "avg": _avg(orders, today)}

    # LLM 사용량: 프록시 기록(MCP) 우선, 못 받으면 봇 집계(같은 서버의 storage/llm_usage.db를 바로 읽는다)
    try:
        from ai import llm_usage
        bot = llm_usage.by_day(AVG_DAYS + 1)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 봇 LLM 사용량 읽기 실패: {e}")
        bot = {}
    proxy = (fetched.get("llm") or {}).get("proxy")
    if not (proxy or {}).get("days") and same_day and (prev.get("llm") or {}).get("source") == "LiteLLM 전체":
        data["llm"] = prev["llm"]  # 이번에만 프록시 조회 실패 - 같은 날엔 출처를 섞지 않는다
    else:
        summary = llm_summary(proxy, bot, today)
        if summary:
            data["llm"] = summary

    _save(data)
    print(f"[통계] 지표 갱신: 생산 {data.get('production', {}).get('today', '?')} / "
          f"판매 {data.get('sales', {}).get('today', '?')} / 주문 {data.get('orders', {}).get('today', '?')} / "
          f"LLM {data.get('llm', {}).get('today', '?')}({data.get('llm', {}).get('source', '-')})")


# ---------------------------------------------------------------- 읽기

def _today() -> dict:
    data = _load()
    return data if data.get("date") == datetime.now(KST).strftime("%Y-%m-%d") else {}


PART_KEYS = ("plex", "books", "books_adult")
_PART_LABELS = {"plex": "영상", "books": "자료실 신간(권)", "books_adult": "스페셜 라인(권)"}


def part(key: str) -> dict | None:
    """[담당 업무] 생산 부문 하나의 오늘 값 {today, avg, saturated, pace, label}. 집계 전이면 None."""
    x = _today().get("production", {}).get("parts", {}).get(key)
    if not x:
        return None
    return {**x, "label": _PART_LABELS.get(key, key),
            "pace": "" if x.get("saturated") else pace(x["today"], x.get("avg"))}


def count(key: str) -> dict | None:
    """[담당 실적] 하루 누적 지표 하나 {today, avg, saturated, pace} - 생산 부문(plex/books/books_adult),
    "sales"(오늘 재생=출하), "orders"(오늘 사람 메시지=고객 문의), "llm_requests"/"llm_tokens"(LLM 요청/토큰).
    집계 전이면 None."""
    if key in PART_KEYS:
        return part(key)
    if key in ("llm_requests", "llm_tokens"):
        x = _today().get("llm")
        if not isinstance(x, dict):
            return None
        today, avg = (x["today"], x.get("avg")) if key == "llm_requests" else (x["tokens_today"], x.get("tokens_avg"))
        return {"today": int(today), "avg": avg, "saturated": False, "pace": pace(int(today), avg),
                "failures": x.get("failures_today", 0), "source": x.get("source", "")}
    x = _today().get(key)
    if not isinstance(x, dict) or "today" not in x:
        return None
    return {"today": int(x["today"]), "avg": x.get("avg"), "saturated": False, "pace": pace(int(x["today"]), x.get("avg"))}


def streams() -> int | None:
    """[담당 실적] 지금 시청 중인 스트림 수(매장 손님). 집계 전이면 None."""
    x = _today().get("sales")
    return int(x.get("streams", 0)) if isinstance(x, dict) else None


def production() -> tuple[int, float]:
    """
    (오늘 생산량, 평소 하루 생산량 대비 %). %는 평균을 아는 부문끼리만 비교한다 - 평균을 모르는
    부문까지 기본 목표로 나누면 신간이 한꺼번에 들어온 날 840% 같은 숫자가 나온다.
    """
    p = _today().get("production", {})
    parts = p.get("parts", {})
    count = int(p.get("today", 0))
    known = [x for x in parts.values() if x.get("avg")]
    if known:
        pct = round(sum(x["today"] for x in known) / sum(x["avg"] for x in known) * 100, 1)
    else:
        pct = round(count / DEFAULT_PRODUCTION_TARGET * 100, 1) if DEFAULT_PRODUCTION_TARGET else 0.0
    if any(x.get("saturated") for x in parts.values()):
        # 받아온 한도가 전부 오늘 것 = 하루치로는 평소보다 확실히 많이 들어온 날
        pct = max(pct, 200.0)
    return count, pct


def _production_line(p: dict) -> str:
    parts = p.get("parts", {})
    more = "+" if any(x.get("saturated") for x in parts.values()) else ""
    details = []
    for key, x in parts.items():
        d = f"{_PART_LABELS.get(key, key)} {x['today']}건{'+' if x.get('saturated') else ''}"
        if x.get("avg") is not None:
            extra = [f"최근 7일 하루 평균 {x['avg']:g}건", "" if x.get("saturated") else pace(x["today"], x["avg"])]
            d += f" ({', '.join(e for e in extra if e)})"
        if x.get("saturated"):
            d += " - 오늘 한꺼번에 대량 입고"
        details.append(d)
    return f"생산(신규 입고) {p.get('today', 0)}건{more}: " + ", ".join(details)


def sales() -> int:
    return int(_today().get("sales", {}).get("today", 0))


def orders() -> int:
    return int(_today().get("orders", {}).get("today", 0))


def pace(today: int, avg: float | None, now: datetime | None = None) -> str:
    """하루 평균을 지금 시각까지로 비례 환산해서 "평소보다 바쁨/평소 수준/한산함"을 고른다."""
    if not avg:
        return ""
    now = now or datetime.now(KST)
    expected = avg * max(now.hour + now.minute / 60, 2.4) / 24  # 새벽엔 기대치가 0에 가까워 하한
    ratio = today / expected if expected else 0
    if ratio >= 1.5:
        return "평소보다 훨씬 바쁨"
    if ratio >= 0.7:
        return "평소 수준"
    return "평소보다 한산함"


def describe() -> str:
    """이야기용 한 줄. 집계된 게 없으면 빈 문자열."""
    data = _today()
    parts = []
    if data.get("production"):
        parts.append(_production_line(data["production"]))
    for key, label in (("sales", "출하(판매)"), ("orders", "주문·고객 문의")):
        m = data.get(key)
        if not m:
            continue
        line = f"{label} {m['today']}건"
        if m.get("avg") is not None:
            extra = [f"최근 7일 하루 평균 {m['avg']:g}건", pace(m["today"], m["avg"])]
            line += f" ({', '.join(e for e in extra if e)})"
        if m.get("streams"):
            line += f", 지금 매장 손님 {m['streams']}명"
        parts.append(line)
    return " / ".join(parts)
