"""에이전트 한 회차: 판단 -> 대화(시간당 상한, 한 사람 한 대화) -> 동네 풍경. 가짜 LLM으로."""
import logging
import random
import re

from poring_food import agents, characters

PAIRS = {"홍두식": "윤혜진", "양관식": "전광례", "최은철": "표미선", "부상길": "여화정", "양금명": "박충섭"}


def _handler(system, user):
    m = re.search(r"'([^']+)'", system)
    me = m.group(1) if m else ""
    if "이번 한 시간 동안" in user:
        contact = {"to": PAIRS[me], "how": "직접", "opening": f"{me}의 첫마디"} if me in PAIRS else None
        return {"location": "카페 오후세시", "activity": "일", "state": "일하는 중", "thought": "흠", "plan": "",
                "contact": contact}
    if "다음 한마디" in user:
        return {"say": "응", "end": True}
    return {}


def _quiet(monkeypatch):
    for k in ("GOSSIP_P_SAME_PLACE", "GOSSIP_P_COLLEAGUE", "GOSSIP_P_REGULAR", "GOSSIP_P_OTHER"):
        monkeypatch.setattr(agents, k, 0)
    monkeypatch.setattr(agents, "TYPING_DELAY", (0, 0))
    monkeypatch.setattr(agents.world, "tick", lambda *a, **k: [])
    monkeypatch.setattr(agents.chronicle, "tick", lambda: None)
    monkeypatch.setattr(agents.kpi, "reading", lambda p: None)
    monkeypatch.setattr(agents.rotation, "tick", lambda *a, **k: [])
    monkeypatch.setattr(agents.newcomers, "due", lambda *a, **k: False)
    base = agents.load_agents

    def awake():
        out = base()
        for a in out:
            a["sleep"], a["decide_every"] = [3, 4], 1
        return out
    monkeypatch.setattr(agents, "load_agents", awake)


def _talks(caplog):
    lines = [r.getMessage() for r in caplog.records if "마디:" in r.getMessage()]
    caplog.clear()
    return lines


def test_conversations_per_hour_cap(fake_llm, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    fake_llm(_handler)
    agents.tick(characters.load_roster())
    talks = _talks(caplog)
    assert len(talks) == agents.MAX_CONVOS_PER_HOUR
    people = [n for t in talks for n in re.findall(r"(\S+)[와과] (\S+)의", t)[0]]
    assert len(people) == len(set(people))  # 한 사람은 한 시간에 한 대화
    agents.tick(characters.load_roster())  # 같은 시간 다시 돌아도 대화가 늘지 않는다
    assert not _talks(caplog)


def test_town_view_lists_everyone(fake_llm, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    fake_llm(_handler)
    agents.tick(characters.load_roster())
    states = characters.update_all_states(characters.load_roster(), random.Random(1))
    caplog.clear()
    agents.post_town_view(states)
    out = "\n".join(r.getMessage() for r in caplog.records)
    assert "지금 동네는" in out and "이맏이" in out  # 배경 인물도 보인다
