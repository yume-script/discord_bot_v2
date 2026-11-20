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


def test_falls_back_to_default_model(monkeypatch, tmp_path):
    """추론 모델(BOA_AGENT_MODEL)이 실패하면(무료 한도·도구 미지원) 그 판단은 기본 모델로, 어느 모델로 했는지 기록"""
    monkeypatch.setattr(ba, "STATE_PATH", str(tmp_path / "boa_agent.json"))

    class Broken(FakeModel):
        def invoke(self, messages):
            raise RuntimeError("429 rate limit (free tier)")

    class Empty(FakeModel):
        def invoke(self, messages):
            return AIMessage(content="")   # 추론만 하다 잘림 - 빈 답도 실패로

    for bad in (Broken(), Empty()):
        calls, posts = [], []
        good = FakeModel()

        def call(name, args):
            calls.append(name)
            return {"changed": {"PARALLEL_TITLES": {"before": 2, "after": 3}}} if name == "wtm_set_settings" else {}
        res = ba.run(call=call, model=bad, fallback=good, stage=0, watching="", post=posts.append)
        assert res["changes"] == ["wtm_set_settings: PARALLEL_TITLES 2 → 3"]
        assert res["model"].startswith("기본 모델") and "실패" in res["model"]
        assert "기본 모델" in posts[0] and "기본 모델" in ba.recent_text()


def test_review_changes_nothing(monkeypatch, tmp_path):
    """점검(대화의 '계획 괜찮아?'): 쓰기는 BOA에 안 보내고 모두 제안으로, 채널·기록에도 안 남김"""
    monkeypatch.setattr(ba, "STATE_PATH", str(tmp_path / "boa_agent.json"))
    calls, posts = [], []
    res = ba.run(call=lambda n, a: calls.append(n) or {}, model=FakeModel(), stage=0, watching="", post=posts.append, review=True)
    assert res["review"] and res["changes"] == [] and len(res["proposals"]) == 2
    assert "wtm_set_settings" not in calls and "tgup_set_settings" not in calls
    assert posts == [] and ba.due()    # 기록하지 않음 - 정기 판단은 그대로


def test_check_tool_needs_no_confirm():
    from core.tool_policy import is_server_affecting
    assert not is_server_affecting("check_download_plan")


def test_compact_keeps_valid_json():
    """긴 wtm_status(로그·기록·대기열)를 잘라도 JSON이 끊기지 않고, 로그는 최근 것을 남긴다"""
    big = {"job": {"running": True, "message": "x" * 1000},
           "log_tail": [f"[11-20 13:{i:02d}] 로그 {i}" for i in range(60)],
           "kakao_queue": [{"title": f"작품{i}", "at": i} for i in range(300)],
           "history": [{"title": f"회차{i}"} for i in range(500)]}
    out = ba.compact(big, limit=3000)
    data = json.loads(out)                                          # 끊긴 JSON이 아님
    assert len(out) <= 3000
    assert data["log_tail"][-2].endswith("로그 59") and "최근" in data["log_tail"][-1]
    assert data["kakao_queue"][0]["title"] == "작품0" and "전체 300개" in data["kakao_queue"][-1]
    assert data["job"]["message"].endswith("…")
    assert "데이터 오류 아님" in ba.compact("가" * 6000, limit=100)
