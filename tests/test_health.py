"""이상 감지: 문제가 이어지면 카톡으로 원인을 짚어 알리고, 같은 원인은 몇 시간에 한 번만, 풀리면 회복 알림."""
from datetime import datetime, timedelta

from poring_food import agents, health, llm, outbox


def _tick(errors=None, decided=0, ok=0, made=5):
    health.begin()
    llm.ERRORS.update(errors or {})
    agents.LAST_TICK.update(decided=decided, ok=ok)
    if made:
        outbox.SCHEDULED["agent"] = made


def test_litellm_down_alerts_after_two_ticks_then_recovers():
    sent = []
    t = datetime(2026, 10, 12, 10, 3)
    _tick({f"0:{llm.LLM_MODEL}": 12}, decided=10, ok=0, made=0)
    assert health.check(300, now=t, send=sent.append) == ""          # 한 번은 일시적일 수 있다
    _tick({f"0:{llm.LLM_MODEL}": 12}, decided=10, ok=0, made=0)
    text = health.check(300, now=t + timedelta(hours=1), send=sent.append)
    assert "LiteLLM 연결 안 됨" in text and "판단 실패" in text and "2회차 연속" in text
    _tick({f"0:{llm.LLM_MODEL}": 12}, decided=10, ok=0, made=0)
    assert "LiteLLM" not in health.check(300, now=t + timedelta(hours=2), send=sent.append)  # 6시간 안엔 다시 안 알림
    _tick(decided=10, ok=10)
    text = health.check(300, now=t + timedelta(hours=3), send=sent.append)
    assert text.startswith("✅") and "LiteLLM 연결" in text and "판단 실패" in text
    assert len(sent) == 2


def test_agent_model_forbidden_alerts_at_once_and_smart_quota_is_ignored():
    sent = []
    _tick({"403:groq_free": 2, f"429:{llm.SMART_MODEL}": 5})
    text = health.check(60, now=datetime(2026, 10, 12, 11, 3), send=sent.append)
    assert "groq_free" in text and "403" in text
    assert "한도" not in text


def test_silent_only_in_waking_hours_and_slow_tick():
    sent = []
    for h in (2, 3, 4):
        _tick(made=0)
        assert health.check(60, now=datetime(2026, 10, 12, h, 3), send=sent.append) == ""
    for h in (9, 10, 11):
        _tick(made=0)
        text = health.check(60, now=datetime(2026, 10, 12, h, 3), send=sent.append)
    assert "대화/글이 하나도" in text
    _tick()
    assert "회차가 50분" in health.check(50 * 60, now=datetime(2026, 10, 12, 12, 3), send=sent.append)


def test_tick_failure_twice():
    sent = []
    _tick(made=0)
    health.check(10, failed=True, now=datetime(2026, 10, 12, 13, 3), send=sent.append)
    _tick(made=0)
    assert "회차가 예외로" in health.check(10, failed=True, now=datetime(2026, 10, 12, 14, 3), send=sent.append)
