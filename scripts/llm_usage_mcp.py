# -*- coding: utf-8 -*-
"""
LLM 사용량 MCP - LiteLLM 프록시의 날짜별 요청 수/토큰과, 이 봇이 직접 센 요청 수를 읽기 전용으로 노출한다.

- 프록시 숫자: LiteLLM 프록시의 사용 기록 API (같은 프록시를 쓰는 다른 서비스까지 포함한 전체).
  프록시에 DB(DATABASE_URL)가 연결돼 있어야 기록이 있다. 새 API(/user/daily/activity)를 먼저 보고,
  없으면 예전 API(/global/activity)를 본다. 조회에는 master 키가 필요하다.
- 봇 숫자: ai/llm_usage.py가 storage/llm_usage.db에 센 것 (이 봇만, 출처별).

[안전] 읽기 전용, 키는 응답에 넣지 않는다. 표준 라이브러리 + mcp만 쓴다.

[환경변수]
  LITELLM_BASE_URL  "http://host:4000" 또는 ".../v1"
  LITELLM_API_KEY   LiteLLM master 키
  LLM_USAGE_DB_PATH 기본 storage/llm_usage.db
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

KST = timezone(timedelta(hours=9))
TIMEOUT_SEC = 15


def _base_url() -> str:
    url = os.getenv("LITELLM_BASE_URL", "").strip().rstrip("/")
    return re.sub(r"/v1$", "", url)


def _get(path: str, params: dict) -> dict:
    url = f"{_base_url()}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.getenv('LITELLM_API_KEY', '')}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:
        return json.loads(res.read().decode("utf-8"))


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _iso_day(raw, year: int) -> str | None:
    """"2026-10-11" / "2026-10-11T..." / "Oct 11" -> "2026-10-11"."""
    s = str(raw or "")
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    try:
        return datetime.strptime(f"{year} {s}", "%Y %b %d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_daily_activity(data: dict) -> dict:
    """/user/daily/activity 응답 -> {날짜: {"requests", "failures", "tokens", "spend"}}."""
    out = {}
    for r in (data or {}).get("results") or []:
        day = _iso_day(r.get("date"), 0)
        m = r.get("metrics") or {}
        if day:
            out[day] = {"requests": int(_num(m.get("api_requests"))), "failures": int(_num(m.get("failed_requests"))),
                        "tokens": int(_num(m.get("total_tokens"))), "spend": round(_num(m.get("spend")), 4)}
    return out


def parse_global_activity(data: dict, year: int) -> dict:
    """/global/activity 응답 -> {날짜: {"requests", "tokens"}}."""
    out = {}
    for r in (data or {}).get("daily_data") or []:
        day = _iso_day(r.get("date"), year)
        if day:
            out[day] = {"requests": int(_num(r.get("api_requests"))), "tokens": int(_num(r.get("total_tokens")))}
    return out


def proxy_usage(days: int) -> dict:
    """{"source": 쓴 API, "days": {...}} 또는 {"error": 이유}."""
    if not _base_url() or not os.getenv("LITELLM_API_KEY"):
        return {"error": "LITELLM_BASE_URL/LITELLM_API_KEY 없음"}
    now = datetime.now(KST)
    params = {"start_date": (now - timedelta(days=days - 1)).strftime("%Y-%m-%d"), "end_date": now.strftime("%Y-%m-%d")}
    errors = []
    for path, parse in (("/user/daily/activity", lambda d: parse_daily_activity(d)),
                        ("/global/activity", lambda d: parse_global_activity(d, now.year))):
        try:
            extra = {"page": 1, "page_size": 100} if path == "/user/daily/activity" else {}
            parsed = parse(_get(path, {**params, **extra}))
        except urllib.error.HTTPError as e:
            errors.append(f"{path} HTTP {e.code}")
            continue
        except Exception as e:  # noqa: BLE001
            errors.append(f"{path} {type(e).__name__}")
            continue
        if parsed:
            return {"source": path, "days": parsed}
        errors.append(f"{path} 기록 없음")
    return {"error": ", ".join(errors)}


def get_llm_usage(days: int = 8) -> dict:
    """
    최근 며칠(오늘 포함, KST)의 LLM 사용량.
    proxy: LiteLLM 프록시 전체 {"source", "days": {날짜: {requests, tokens, failures?, spend?}}} 또는 {"error"}
    bot: 이 디스코드 봇이 직접 센 것 {날짜: {requests, failures, tokens, sources: {출처: 요청 수}}}
    """
    days = max(1, min(int(days or 8), 31))
    try:
        from ai import llm_usage
        bot = llm_usage.by_day(days)
    except Exception as e:  # noqa: BLE001
        bot = {"error": type(e).__name__}
    return {"proxy": proxy_usage(days), "bot": bot}


if __name__ == "__main__":
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("LLM Usage")
    mcp.tool()(get_llm_usage)
    mcp.run(transport="stdio")
