"""
이 봇이 보낸 LLM(LiteLLM 프록시) 요청 수를 직접 센다 - storage/llm_usage.db (SQLite).

LiteLLM 프록시도 자기 사용 기록 API가 있지만(scripts/llm_usage_mcp.py가 읽는다), 프록시에 DB가 없거나
조회가 실패해도 이 봇이 얼마나 썼는지는 알 수 있게 봇 쪽에서도 센다. 프록시 숫자는 같은 LiteLLM을 쓰는
다른 서비스까지 포함한 전체, 여기 숫자는 이 봇(채팅/포링푸드/...)만이다.

- 호출하는 곳: poring_food/llm.py(포링푸드), ai/llm_client.build_chat_model의 콜백(채팅/RAG/MBTI/...)
- 하루(KST)·출처별로 요청 수/실패 수/토큰을 더한다. 기록 실패는 조용히 넘긴다 (LLM 호출을 막지 않게).
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("LLM_USAGE_DB_PATH") or os.path.join(_BASE, "storage", "llm_usage.db")
_SCHEMA = """CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, source TEXT NOT NULL, requests INTEGER DEFAULT 0, failures INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, PRIMARY KEY (day, source))"""


def _connect(path: str | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute(_SCHEMA)
    return conn


def record(source: str, ok: bool = True, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """요청 하나를 센다 (실패한 요청도 요청이다)."""
    day = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        conn = _connect()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO usage (day, source, requests, failures, prompt_tokens, completion_tokens) "
                    "VALUES (?, ?, 1, ?, ?, ?) ON CONFLICT(day, source) DO UPDATE SET "
                    "requests = requests + 1, failures = failures + excluded.failures, "
                    "prompt_tokens = prompt_tokens + excluded.prompt_tokens, "
                    "completion_tokens = completion_tokens + excluded.completion_tokens",
                    (day, source, 0 if ok else 1, int(prompt_tokens or 0), int(completion_tokens or 0)))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 사용량 기록 때문에 LLM 호출이 실패하면 안 된다
        pass


def by_day(days: int = 8, path: str | None = None) -> dict[str, dict]:
    """최근 days일(오늘 포함) {날짜: {"requests", "failures", "tokens", "sources": {출처: 요청 수}}}."""
    since = (datetime.now(KST) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    path = path or DB_PATH
    if not os.path.exists(path):
        return {}
    conn = _connect(path)
    try:
        rows = conn.execute("SELECT day, source, requests, failures, prompt_tokens + completion_tokens "
                            "FROM usage WHERE day >= ? ORDER BY day", (since,)).fetchall()
    finally:
        conn.close()
    out: dict[str, dict] = {}
    for day, source, req, fail, tokens in rows:
        d = out.setdefault(day, {"requests": 0, "failures": 0, "tokens": 0, "sources": {}})
        d["requests"] += req
        d["failures"] += fail
        d["tokens"] += tokens
        d["sources"][source] = req
    return out


def _usage_from_result(response) -> tuple[int, int]:
    """LangChain LLMResult -> (prompt, completion) 토큰. 모르면 (0, 0)."""
    usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    p, c = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    if not (p or c):
        for gens in getattr(response, "generations", None) or []:
            for g in gens:
                meta = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                p += meta.get("input_tokens", 0)
                c += meta.get("output_tokens", 0)
    return int(p or 0), int(c or 0)


def callback(source: str = "봇"):
    """ChatOpenAI(callbacks=[...])에 넣는 LangChain 콜백 - LLM 호출이 끝나거나 실패할 때마다 센다."""
    from langchain_core.callbacks import BaseCallbackHandler

    class _UsageCallback(BaseCallbackHandler):
        def on_llm_end(self, response, **kwargs):
            record(source, True, *_usage_from_result(response))

        def on_llm_error(self, error, **kwargs):
            record(source, False)

    return _UsageCallback()
