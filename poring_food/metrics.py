"""
포링푸드 "생산/판매/주문" 지표 - 실제 서비스 수치를 공장 실적으로 바꾼다.

| 이야기 지표          | 실제 데이터                                   | 가져오는 곳                          |
|---------------------|----------------------------------------------|-------------------------------------|
| 생산량(신규 입고)     | 오늘 Plex 신규 등록 + 북오아시스 신간(시리즈)    | plex get_recently_added,            |
|                     |                                              | bookoasis search_books(date_desc)   |
| 판매량(출하)         | 오늘 Plex 재생 수, 지금 시청 중인 수(매장 손님)  | tautulli plays_by_date / activity   |
| 주문·고객 문의        | 오늘 사람이 봇에게 보낸 메시지 수               | 대화 로그 DB (예전 "생산량")         |

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
    BOOKOASIS_ENABLED, BOOKOASIS_MCP_SERVER, BOOKOASIS_STORY_DB_TYPES, BOOKOASIS_TIMEOUT_SEC,
    DISCORD_BOT_V2_DB_PATH, STATE_DIR,
)

KST = timezone(timedelta(hours=9))
METRICS_PATH = os.path.join(STATE_DIR, "metrics.json")
PLEX_SERVER = os.getenv("PORING_PLEX_MCP_SERVER", "plex")
TAUTULLI_SERVER = os.getenv("PORING_TAUTULLI_MCP_SERVER", "tautulli")
PLEX_FETCH_LIMIT = int(os.getenv("PORING_PLEX_FETCH_LIMIT", "50"))
BOOK_FETCH_LIMIT = int(os.getenv("PORING_BOOK_FETCH_LIMIT", "30"))
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


async def _books_async() -> tuple[dict, str]:
    from ai.mcp_manager import server_session
    total: dict[str, int] = {}
    complete_from = "0000-00-00"
    async with server_session(BOOKOASIS_MCP_SERVER) as session:
        for db_type in BOOKOASIS_STORY_DB_TYPES:
            res = await session.call_tool(
                "search_books", {"db_type": db_type, "sort": "date_desc", "limit": BOOK_FETCH_LIMIT}
            )
            if getattr(res, "isError", False):
                continue
            series = _parse_tool_result(res).get("series", []) or []
            dates = [str(s.get("latest_added", ""))[:10] for s in series if s.get("latest_added")]
            counts, frm = daily_counts(dates, fetched_full=len(series) >= BOOK_FETCH_LIMIT)
            for d, n in counts.items():
                total[d] = total.get(d, 0) + n
            complete_from = max(complete_from, frm)
    return total, complete_from


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
    jobs = {"plex": _plex_async(), "tautulli": _tautulli_async()}
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

    # 생산: Plex + 북오아시스. 못 받은 쪽은 오늘 직전 값 유지.
    parts = {}
    past = _past_days(today)
    for key in ("plex", "books"):
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

    # 판매: Tautulli
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

    _save(data)
    print(f"[통계] 지표 갱신: 생산 {data.get('production', {}).get('today', '?')} / "
          f"판매 {data.get('sales', {}).get('today', '?')} / 주문 {data.get('orders', {}).get('today', '?')}")


# ---------------------------------------------------------------- 읽기

def _today() -> dict:
    data = _load()
    return data if data.get("date") == datetime.now(KST).strftime("%Y-%m-%d") else {}


_PART_LABELS = {"plex": "영상", "books": "자료실 신간"}


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
