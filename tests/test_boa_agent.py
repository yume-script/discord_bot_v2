"""다운로드 에이전트 (poring_food/boa_agent.py) - 안전 범위, 가짜 LLM·가짜 BOA로 한 번 판단"""
import json

from langchain_core.messages import AIMessage

from poring_food import boa_agent as ba


def test_guard():
    st = {}
    ok = lambda name, args, stage=0: ba.guard(name, args, stage, st) == ""  # noqa: E731
    assert ok("wtm_set_settings", {"values": {"PARALLEL_TITLES": 3, "SCHEDULE_CRON": "0 */2 * * *", "KAKAO_USE_WAITFREE": True}})
    assert not ok("wtm_set_settings", {"values": {"PARALLEL_TITLES": 6}})                    # 범위 밖
    assert not ok("wtm_set_settings", {"values": {"PARALLEL_TITLES": 3}}, stage=2)           # 바쁠 땐 2까지
    assert not ok("wtm_set_settings", {"values": {"FAST_MODE": True}}, stage=2)
    assert ok("wtm_set_settings", {"values": {"FAST_MODE": True}}, stage=1)
    assert not ok("wtm_set_settings", {"values": {"SCHEDULE_CRON": "*/10 * * * *"}})        # 너무 잦음
    assert not ok("wtm_set_settings", {"values": {"KAKAO_USE_PAID_TICKETS": True}})          # 돈 - 관리자
    assert not ok("wtm_set_settings", {"values": {"DELAY_SECONDS": 0.1}})
    assert ok("tgup_set_settings", {"settings": {"encode_slots": 2, "hours": "1-7"}})
    assert not ok("tgup_set_settings", {"settings": {"encode_slots": 2}}, stage=2)
    assert not ok("tgup_set_settings", {"settings": {"encode_slots": 4}})
    assert not ok("wtm_run", {"job": "run_full_cycle_now"}, stage=2)
    st["last_run"] = {"scan_now": __import__("time").time() - 600}
    assert not ok("wtm_run", {"job": "scan_now"}) and ok("wtm_run", {"job": "run_full_cycle_now"}) and ok("wtm_run", {"job": "cancel_job"})
    st["subs_this_run"] = ba.MAX_SUBSCRIPTION_CHANGES
    assert not ok("wtm_set_subscription", {"titleId": "1", "state": "subscribe"})


class FakeModel:
    """첫 답: 설정 둘(하나는 범위 밖) 바꾸기, 둘째 답: 요약"""
    def __init__(self):
        self.n = 0

    def bind_tools(self, tools):
        self.tools = {t.name for t in tools}
        return self

    def invoke(self, messages):
        self.n += 1
        if self.n == 1:
            return AIMessage(content="", tool_calls=[
                {"id": "a", "name": "wtm_set_settings", "args": {"values": {"PARALLEL_TITLES": 3}}},
                {"id": "b", "name": "tgup_set_settings", "args": {"settings": {"encode_slots": 4}}}])
        return AIMessage(content="대기열이 밀려 동시 작품 수를 3으로 올림. 제안: 인코딩 4는 범위 밖.")


def test_run_applies_and_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "STATE_PATH", str(tmp_path / "boa_agent.json"))
    calls, posts = [], []

    def call(name, args):
        calls.append(name)
        if name == "wtm_set_settings":
            return {"message": "저장", "changed": {"PARALLEL_TITLES": {"before": 2, "after": 3}}}
        return {"ok": True}
    model = FakeModel()
    res = ba.run(call=call, model=model, stage=0, watching="재생 0", post=posts.append)
    assert {"wtm_status", "wtm_forecast", "tgup_status", "tgup_settings"} <= set(calls)   # 지금 상황
    assert "wtm_set_settings" in calls and "tgup_set_settings" not in calls                # 범위 밖은 BOA에 안 보냄
    assert res["changes"] == ["wtm_set_settings: PARALLEL_TITLES 2 → 3"]
    assert len(res["proposals"]) == 1 and "encode_slots=4" in res["proposals"][0]
    assert len(posts) == 1 and "적용함" in posts[0] and "제안" in posts[0]
    saved = json.load(open(tmp_path / "boa_agent.json", encoding="utf-8"))
    assert saved["runs"][-1]["changes"] == res["changes"] and not ba.due()
    assert "PARALLEL_TITLES 2 → 3" in ba.recent_text()
    assert {"wtm_set_settings", "tgup_set_settings", "wtm_run", "wtm_set_subscription", "tgup_status"} <= model.tools


def test_quiet_when_nothing_changed(monkeypatch, tmp_path):
    monkeypatch.setattr(ba, "STATE_PATH", str(tmp_path / "boa_agent.json"))

    class Calm(FakeModel):
        def invoke(self, messages):
            return AIMessage(content="지금 계획 그대로가 좋음.")
    posts = []
    res = ba.run(call=lambda n, a: {}, model=Calm(), stage=0, watching="", post=posts.append)
    assert res["changes"] == [] and posts == []   # 바꾼 것·제안이 없으면 채널에 안 올림
