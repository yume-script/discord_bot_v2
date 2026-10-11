"""모임: 결론 없으면 다음 날 자동 소집, 5차에서도 안 나면 투표, 결과는 영구 기억 + "정해진 것"."""
import re

from poring_food import agents, chronicle, memory


def _handler(votes):
    def h(system, user):
        if "모임 기록 담당이다. 실제로" in system:
            return {"decided": False, "conclusion": "후보만 나옴", "detail": "의견 갈림", "next": "다시 모이자",
                    "candidates": ["쉼표 마을", "포근동"]}
        if "실제로 해 나갈 계획" in system:
            return {"is_project": False}
        me = re.search(r"'([^']+)'", system).group(1)
        if "한 표를 던져라" in user:
            return {"vote": votes(me), "reason": "그냥"}
        if "모임에서 다음 발언" in user:
            return {"say": "제 생각은요", "stance": "제안", "proposal": ""}
        return {}
    return h


def _awake(monkeypatch):
    monkeypatch.setattr(agents, "TYPING_DELAY", (0, 0))
    base = agents.load_agents

    def la():
        out = base()
        for a in out:
            a["sleep"] = [3, 4]
        return out
    monkeypatch.setattr(agents, "load_agents", la)


def test_undecided_meeting_schedules_next_round(fake_llm, monkeypatch):
    _awake(monkeypatch)
    fake_llm(_handler(lambda me: "포근동"))
    res = agents.run_meeting("마을 이름 짓기", None, "", 4)
    assert res["round"] == 1 and not res["decided"] and res["next_at"]
    assert chronicle.meeting_state("마을 이름 짓기")["round"] == 1


def test_fifth_round_ends_with_vote_and_permanent_memory(fake_llm, monkeypatch):
    _awake(monkeypatch)
    chronicle.schedule_meeting("마을 이름 짓기", 4, "", [], 4, "2000-01-01T20:00", "4차 결론 없음")
    fake_llm(_handler(lambda me: "포근동" if len(me) % 2 else "쉼표 마을"))
    res = agents.run_meeting("마을 이름 짓기", None, "", 4)
    assert res["round"] == 5 and res["decided"] and res["vote"]
    winner = res["vote"]["winner"]
    assert any(t["title"] == "마을 이름 짓기" and t.get("permanent") for t in chronicle.facts())
    some = agents.load_agents()[0]["name"]
    assert any(m.get("permanent") and winner in m["text"] for m in memory._load(some))
    assert chronicle.meeting_state("마을 이름 짓기") is None  # 더 이상 모이지 않는다
