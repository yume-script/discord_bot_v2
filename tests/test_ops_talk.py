"""서버 관리 대화 (poring_food/ops_talk.py) + 역할 있는 인물만 (agents.ROLE_ONLY) + 빌런 끔"""
import random
from datetime import datetime

from poring_food import agents, gdw_queue, ops_talk, stewards, villains
from poring_food.clock import KST

T0 = datetime(2026, 11, 20, 14, 0, tzinfo=KST)


def test_role_only_roster(monkeypatch):
    monkeypatch.setattr(agents, "ROLE_ONLY", True)
    names = {a["name"] for a in agents.load_agents()}
    assert names == stewards.role_names() & {a["name"] for a in agents._base_agents()}
    assert {"애순이", "강태오", "권지아", "민재현"} <= names
    assert not names & {"윤채아", "장만석", "백상만", "안대풍", "문하늘"}    # 역할 없는 인물·빌런은 에이전트에서 빠짐
    assert stewards.roles_of("이나연") == ["ff", "ym_player"]


def test_villains_off_by_default():
    import importlib
    import os
    old = os.environ.pop("PORING_VILLAINS")
    try:
        importlib.reload(villains)
        assert villains.ENABLED is False
    finally:
        os.environ["PORING_VILLAINS"] = old
        importlib.reload(villains)


def test_detect_issues(monkeypatch):
    monkeypatch.setattr(gdw_queue, "URL", "")
    calls = {"tgup_status": {"queue_total": 55, "running": [], "throttle_warnings": ["첫 계정: 텔레그램이 업로드를 늦춤"]},
             "wtm_status": {"job": {"last_error": None}, "log_tail": ["[13:00] 카카오 429 Too Many Requests"]}}
    found = ops_talk.detect(T0, call=lambda n, a=None: calls.get(n), gdw={"level": 1, "alive": True, "pending": 40,
                            "failed": 0, "oldest_min": 70}, waitfree_issue=lambda: "추천 02~12시 - 지금 10~20시")
    assert [f["key"] for f in found] == ["upload_throttle", "upload_backlog", "gdw_backlog", "wtm_trouble", "waitfree_window"]
    assert ops_talk.detect(T0, call=lambda n, a=None: {}, gdw={"level": 0, "alive": True}, waitfree_issue=lambda: "") == []


def test_talk_executes_and_cooldown(monkeypatch):
    """대화 → 결론 조치 실행 → 기록, 같은 이슈는 쿨다운, 판단착오는 기록"""
    monkeypatch.setattr(agents, "ROLE_ONLY", True)
    seen = {}

    def council(topic, facts, parties, spec, rounds=1, speak=None, **k):
        seen["parties"], seen["spec"], seen["facts"] = parties, spec, facts
        return {"decision": {"actions": [{"do": "download_agent", "note": "동시 업로드 줄이기"}, {"do": "waitfree", "start": 2, "end": 12}],
                             "reason": "대기 55개", "mistake": "민재현이 대기를 5개로 잘못 읽음", "say": "민재현 말은 숫자가 틀렸어요."},
                "chair": "애순이"}
    ran = []

    def execute(acts, allowed, title, reason):
        ran.append(([a["do"] for a in acts], allowed))
        return [{"do": a["do"], "ok": a["do"] in allowed} for a in acts]
    cur = {"stage": 0, "upload_queue": 55, "upload_limited": 0, "gdw_pending": 3}
    rec = ops_talk.talk({"key": "upload_backlog", "facts": "대기 55개"}, T0, council=council, execute=execute, cur=cur,
                        rng=random.Random(0))
    assert seen["parties"][0] == "upload" and "download_agent" in seen["spec"] and "waitfree" not in seen["spec"]
    assert "올리기 대기 55개" in seen["facts"]
    assert ran == [(["download_agent", "waitfree"], ["download_agent", "none"])]   # 이 이슈에선 기다무 조치는 안 됨
    assert rec["acted"] and rec["mistake"]
    st = ops_talk._load()
    assert st["mistakes"][-1]["caught"].startswith("민재현")
    detected = [{"key": "upload_backlog", "facts": "x"}, {"key": "gdw_backlog", "facts": "y"}]
    nxt = ops_talk.next_issue(T0.replace(minute=30), detected=detected, cur=cur)
    assert nxt["key"] == "gdw_backlog"    # 같은 이슈는 쿨다운
    assert "판단착오" in ops_talk.recent_text()


def test_retro_when_worse(monkeypatch):
    """결정 RETRO_MIN분 뒤 숫자가 나빠졌으면 회고 이슈 (판단착오)"""
    monkeypatch.setattr(agents, "ROLE_ONLY", True)
    ops_talk._save({"talks": [{"at": T0.isoformat(timespec="minutes"), "issue": "upload_backlog", "title": "텔레그램 올리기 대기열 밀림",
                               "parties": ["upload", "assistant"], "acted": True, "summary": "동시 인코딩 2로",
                               "before": {"stage": 0, "upload_queue": 40, "gdw_pending": 5, "upload_limited": 0}}]})
    later = T0.replace(hour=15)
    assert ops_talk.next_issue(later, detected=[], cur={"stage": 0, "upload_queue": 90, "gdw_pending": 5}) is None  # 아직 2시간 안 됨
    much_later = T0.replace(hour=16, minute=10)
    it = ops_talk.next_issue(much_later, detected=[], cur={"stage": 0, "upload_queue": 90, "gdw_pending": 5, "upload_limited": 0})
    assert it["key"] == "retro" and "텔레그램 올리기 대기 40→90개" in it["facts"]
    rec = ops_talk.talk(it, much_later, council=lambda *a, **k: {"decision": {"actions": [{"do": "none"}], "say": "되돌리자"}},
                        execute=lambda *a: [], cur={"stage": 0})
    assert rec["title"].startswith("회고") and ops_talk._load()["mistakes"][-1]["worse"].startswith("2026") is False
    assert ops_talk.worse_than({"stage": 0, "upload_queue": 10}, {"stage": 1, "upload_queue": 12}) == ""


def test_scheduled_meetings_off(monkeypatch):
    monkeypatch.setattr(agents, "SCHEDULED_MEETINGS", False)
    called = []
    monkeypatch.setattr(agents, "run_meeting", lambda *a, **k: called.append(a))
    agents.run_due_meetings()
    assert called == []


def test_real_council_with_slip(monkeypatch):
    """진짜 stewards.council로: 참석자가 서로의 역할을 보고, 한 사람에게만 판단착오 지시가 간다"""
    from poring_food import llm
    monkeypatch.setattr(agents, "ROLE_ONLY", True)
    monkeypatch.setattr(ops_talk, "MISTAKE_P", 1.0)
    prompts = []

    def fake(system, user, **k):
        prompts.append((k.get("agent"), user))
        if k.get("tag") == "시설 조율 결론":
            return {"actions": [{"do": "none"}], "say": "지켜봐요", "mistake": ""}
        return {"say": "숫자 확인해 보니 대기는 55개예요."}
    monkeypatch.setattr(llm, "llm_json", fake)
    rec = ops_talk.talk({"key": "upload_backlog", "facts": "대기 55개"}, T0, execute=lambda *a: [],
                        cur={"stage": 0, "upload_queue": 55}, rng=random.Random(1))
    speaker_prompts = [u for a, u in prompts if "[참석자와 역할]" in u]
    assert speaker_prompts and all("민재현" in u and "강태오" in u for u in speaker_prompts)   # 서로의 역할
    slipped = [u for u in speaker_prompts if "판단착오" in u]
    assert len(slipped) >= 1 and rec["slip"]
    assert len(speaker_prompts) >= 2 * 3   # 2바퀴
