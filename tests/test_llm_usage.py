"""LLM 사용량: 봇 직접 집계, LiteLLM 프록시 응답 해석(MCP), 지표 요약, 에린 담당 실적(연산 처리/운영 경비)."""
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ai import llm_usage
from poring_food import kpi, metrics

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime("%Y-%m-%d")


def _mcp_script():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "llm_usage_mcp.py")
    spec = importlib.util.spec_from_file_location("llm_usage_mcp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_and_by_day():
    llm_usage.record("채팅", True, 10, 5)
    llm_usage.record("채팅", False)
    llm_usage.record("포링푸드", True, 100, 20)
    d = llm_usage.by_day(3)[TODAY]
    assert d["requests"] == 3 and d["failures"] == 1 and d["tokens"] == 135
    assert d["sources"] == {"채팅": 2, "포링푸드": 1}


def test_usage_from_langchain_result():
    res = SimpleNamespace(llm_output={"token_usage": {"prompt_tokens": 7, "completion_tokens": 3}}, generations=[])
    assert llm_usage._usage_from_result(res) == (7, 3)
    msg = SimpleNamespace(usage_metadata={"input_tokens": 4, "output_tokens": 2})
    res = SimpleNamespace(llm_output=None, generations=[[SimpleNamespace(message=msg)]])
    assert llm_usage._usage_from_result(res) == (4, 2)


def test_poring_llm_counts_requests(fake_llm):
    from poring_food.llm import llm_json
    fake_llm(lambda system, user: {"ok": 1})
    assert llm_json("s", "u") == {"ok": 1}
    assert llm_usage.by_day(1)[TODAY]["sources"] == {"포링푸드": 1}


def test_proxy_response_parsing():
    mod = _mcp_script()
    new = {"results": [{"date": "2026-10-10", "metrics": {"api_requests": 120, "failed_requests": 3,
                                                          "total_tokens": 50000, "spend": 0.12}}]}
    assert mod.parse_daily_activity(new) == {"2026-10-10": {"requests": 120, "failures": 3, "tokens": 50000, "spend": 0.12}}
    old = {"daily_data": [{"date": "Oct 10", "api_requests": 7, "total_tokens": 900}]}
    assert mod.parse_global_activity(old, 2026) == {"2026-10-10": {"requests": 7, "tokens": 900}}
    assert mod.proxy_usage(3).get("error")  # 주소/키 없음


def test_llm_summary_prefers_proxy_then_bot():
    today = "2026-10-11"
    proxy = {"days": {today: {"requests": 50, "tokens": 9000}, "2026-10-10": {"requests": 100, "tokens": 20000}}}
    bot = {today: {"requests": 30, "failures": 2, "tokens": 5000}}
    s = metrics.llm_summary(proxy, bot, today)
    assert s["source"] == "LiteLLM 전체" and s["today"] == 50 and s["avg"] == 100 and s["failures_today"] == 2
    assert s["bot_today"] == 30
    s = metrics.llm_summary({"error": "x"}, bot, today)
    assert s["source"] == "봇 집계" and s["today"] == 30 and s["avg"] is None  # 지난 기록이 없으면 평균 모름
    assert metrics.llm_summary(None, {}, today) is None


def test_erinn_llm_posts(monkeypatch):
    from poring_food import agents, characters
    posts, _ = kpi.assign(agents.load_agents(), characters.load_roster())
    assert [p["id"] for p in posts["최택"]] == ["llm_requests"]
    assert [p["id"] for p in posts["동그라미"]] == ["llm_cost"]
    counts = {"llm_requests": {"today": 40, "avg": 100.0, "saturated": False, "pace": "평소보다 한산함", "failures": 12},
              "llm_tokens": {"today": 1000, "avg": 10000.0, "saturated": False, "pace": "평소보다 한산함"}}
    monkeypatch.setattr(kpi.metrics, "count", lambda k: counts.get(k))
    req = kpi.reading(kpi.POST_BY_ID["llm_requests"])
    assert "처리 40건" in req["text"] and "실패 12건" in req["alert"]
    cost = kpi.reading(kpi.POST_BY_ID["llm_cost"])
    assert "1,000토큰" in cost["text"] and not cost["alert"]  # 몇 시에 돌려도 평소보다 적게 쓴 양
    kpi.record(datetime(2026, 10, 7, 23))
    rows = kpi._load()["daily"]["2026-10-07"]
    assert rows["llm_cost"]["score"] == 200  # 평소의 1/10만 썼다 (상한 200)
    assert rows["llm_requests"]["score"] == 40


def test_chat_model_has_usage_callback():
    import pytest
    pytest.importorskip("langchain_openai")
    from ai.llm_client import build_chat_model
    model = build_chat_model(source="채팅")
    assert any(type(c).__name__ == "_UsageCallback" for c in model.callbacks)
