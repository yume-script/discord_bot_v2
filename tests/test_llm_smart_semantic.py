"""LLM 상위 모델 라우팅(하루 상한, 실패 시 기본 모델, 429 재시도)과 기억 의미 검색."""
import json

from ai import llm_usage
from poring_food import llm, memory, semantic


class _Resp:
    def __init__(self, status, content=None):
        self.status_code = status
        self._content = content

    def json(self):
        if self.status_code != 200:
            return {"error": {"message": f"provider says {self.status_code}"}}
        return {"choices": [{"message": {"content": json.dumps(self._content or {"ok": 1})}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}}


def _route(monkeypatch, statuses: dict):
    """statuses: {모델: [차례로 돌려줄 HTTP 상태...]} - 다 쓰면 200."""
    calls = []
    bodies = _route.bodies = []

    def post(url, json=None, **kw):  # noqa: A002
        calls.append(json["model"])
        bodies.append(json)
        seq = statuses.get(json["model"], [])
        return _Resp(seq.pop(0) if seq else 200, {"model": json["model"]})
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm, "API_URL", "http://llm.invalid/v1/chat/completions")
    monkeypatch.setattr(llm, "LITELLM_MASTER_KEY", "k")
    monkeypatch.setattr(llm, "LLM_MODEL", "base")
    monkeypatch.setattr(llm, "SMART_MODEL", "smart")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    monkeypatch.setattr(llm, "_no_retry_until", 0.0)
    monkeypatch.setattr(llm, "_smart_blocked_day", "")
    monkeypatch.setattr(llm, "_agent_model_blocked", {})
    monkeypatch.setattr(llm, "AGENT_MODELS", {"소라": "groq"})
    return calls


def test_smart_routing_and_daily_cap(monkeypatch):
    calls = _route(monkeypatch, {})
    monkeypatch.setattr(llm, "SMART_DAILY_MAX", 2)
    assert llm.llm_json("s", "u", smart=True) == {"model": "smart"}
    assert llm.llm_json("s", "u") == {"model": "base"}
    assert llm.llm_json("s", "u", smart=True) == {"model": "smart"}
    assert llm.llm_json("s", "u", smart=True) == {"model": "base"}  # 오늘 상한을 다 씀
    assert calls == ["smart", "base", "smart", "base"]
    assert llm.smart_used_today() == 2
    src = next(iter(llm_usage.by_day(1).values()))["sources"]
    assert src == {"포링푸드(상위)": 2, "포링푸드": 2}


def test_smart_failure_falls_back_and_429_retries(monkeypatch):
    calls = _route(monkeypatch, {"smart": [429], "base": [429]})
    assert llm.llm_json("s", "u", smart=True) == {"model": "base"}
    assert calls == ["smart", "base", "base"]  # 상위 모델 한도 초과 -> 기본 모델, 분당 한도 -> 쉬고 한 번 더
    calls.clear()
    _route(monkeypatch, {"base": [500]})
    assert llm.llm_json("s", "u") is None  # 429가 아니면 다시 부르지 않는다


def _fake_encode(texts):
    groups = [("야근", "밤", "늦게", "남아서", "보고서"), ("고양이", "강아지", "산책"), ("비", "우산")]
    out = []
    for t in texts:
        v = [1.0 if any(w in t for w in g) else 0.0 for g in groups] + [0.1]
        n = sum(x * x for x in v) ** 0.5
        out.append([x / n for x in v])
    return out


def test_semantic_recall_finds_meaning_not_words(monkeypatch):
    monkeypatch.setattr(semantic, "ENABLED", True)
    monkeypatch.setattr(semantic, "_encode", _fake_encode)
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_worker", object())  # 백그라운드 스레드 대신 테스트가 직접 채운다
    memory.remember("애순이", "점심에 강아지 영상을 봤다", 5)
    memory.remember("애순이", "어제 밤 11시까지 남아서 보고서를 고쳤다", 5)
    memory.remember("애순이", "출근길에 우산을 놓고 왔다", 5)
    # 처음엔 벡터가 없어서 판단 중에 만들지 않고 백그라운드에 맡긴다 (회차가 늘어지지 않게)
    memory.recall("애순이", [], k=1, query="부장님이 또 야근을 시켰다")
    assert len(semantic._pending) == 3
    assert semantic.process_pending(100) == 3
    top = memory.recall("애순이", [], k=1, query="부장님이 또 야근을 시켰다")
    assert top[0]["text"].startswith("어제 밤 11시")
    assert len(memory.store.memory_vecs([m["id"] for m in memory.store.memories("애순이")])) == 3
    assert not semantic._pending


def test_semantic_off_keeps_keyword_recall(monkeypatch):
    monkeypatch.setattr(semantic, "ENABLED", False)
    memory.remember("애순이", "소라랑 카페에서 수다", 5)
    memory.remember("애순이", "혼자 집에서 쉼", 5)
    top = memory.recall("애순이", ["소라"], k=1, query="아무거나")
    assert "소라" in top[0]["text"]


def test_persistent_429_stops_retrying(monkeypatch):
    calls = _route(monkeypatch, {"smart": [429], "base": [429, 429, 429, 429]})
    assert llm.llm_json("s", "u", smart=True) is None
    assert calls == ["smart", "base", "base"]
    calls.clear()
    assert llm.llm_json("s", "u", smart=True) is None
    assert calls == ["base"]  # 상위 모델은 오늘 막혔고, 기본 모델은 한동안 재시도 없이 바로 실패


def test_parse_agent_models():
    assert llm.parse_agent_models("groq_free:소라, 김동식 ;other:조이서") == {
        "소라": "groq_free", "김동식": "groq_free", "조이서": "other"}
    assert llm.parse_agent_models("") == {}


def test_agent_model_routing_and_fallback(monkeypatch):
    calls = _route(monkeypatch, {})
    assert llm.llm_json("s", "u", agent="소라") == {"model": "groq"}
    assert _route.bodies[-1].get("reasoning_effort") == "low"
    assert llm.llm_json("s", "u", agent="애순이") == {"model": "base"}  # 전용 모델이 없는 인물
    assert "reasoning_effort" not in _route.bodies[-1]
    assert calls == ["groq", "base"]
    src = next(iter(llm_usage.by_day(1).values()))["sources"]
    assert src["포링푸드(groq)"] == 1
    # 분당 한도(429) -> 그 호출은 기본 모델로, 한동안은 전용 모델을 건너뛴다
    calls = _route(monkeypatch, {"groq": [429]})
    assert llm.llm_json("s", "u", agent="소라") == {"model": "base"}
    assert llm.llm_json("s", "u", agent="소라") == {"model": "base"}
    assert calls == ["groq", "base", "base"]


def test_agent_model_forbidden_backs_off_long(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="poring_food")
    calls = _route(monkeypatch, {"groq": [403]})
    assert llm.llm_json("s", "u", agent="소라") == {"model": "base"}
    assert llm.llm_json("s", "u", agent="소라") == {"model": "base"}
    assert calls == ["groq", "base", "base"]  # 거부(403)면 한동안 전용 모델을 아예 안 부른다
    assert llm._agent_model_blocked["groq"] - llm.time.time() > 600
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "HTTP 403 (groq) - provider says 403" in log  # 이유가 로그에 남는다
