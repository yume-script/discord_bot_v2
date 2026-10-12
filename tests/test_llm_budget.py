"""인물 전용 모델의 분당 토큰 예산 - 한도에 가까우면 잠깐 기다리고, 그래도 안 되면 그 호출만 기본 모델로."""
from poring_food import llm


def test_reserve_waits_then_falls_back(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(llm.time, "time", lambda: clock["t"])
    monkeypatch.setattr(llm.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(llm, "AGENT_MODEL_TPM", 10000)
    llm._budget.clear()
    a = llm._reserve("m", 5000)
    assert a is not None
    assert llm._reserve("m", 3000) is not None            # 8000 <= 8500
    assert llm._reserve("m", 3000, wait=5) is None         # 11000 > 8500, 5초 안엔 자리가 안 난다
    llm._settle(a, 1000)                                   # 실제로는 1000만 썼다
    assert llm._reserve("m", 3000, wait=0) is not None     # 이제 7000
    clock["t"] += 10
    b = llm._reserve("m", 5000, wait=60)                   # 기다리면 60초 창이 비면서 자리가 난다
    assert b is not None and clock["t"] >= 1060


def test_llm_json_uses_budget_and_falls_back(fake_llm, monkeypatch):
    monkeypatch.setattr(llm, "AGENT_MODELS", {"소라": "groq_free"})
    monkeypatch.setattr(llm, "AGENT_MODEL_TPM", 1)        # 예산이 없다 -> 첫 호출만(빈 창) 통과, 다음은 기본 모델
    monkeypatch.setattr(llm, "AGENT_MODEL_WAIT_SEC", 0)
    llm._budget.clear()
    llm._agent_model_blocked.clear()
    fake_llm(lambda system, user: {"ok": True})
    models = []
    real = llm._post

    def spy(model, *a, **kw):
        models.append(model)
        return real(model, *a, **kw)

    monkeypatch.setattr(llm, "_post", spy)
    assert llm.llm_json("s", "u" * 100, agent="소라") == {"ok": True}
    assert llm.llm_json("s", "u" * 100, agent="소라") == {"ok": True}
    assert models == ["groq_free", llm.LLM_MODEL]
