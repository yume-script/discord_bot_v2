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
    # 배경 인물도 보인다 (특정 인물 이름으로 확인하면 그 사람이 자는 시간대에 실패해서, 깨어 있는 배경 인물 아무나)
    agent_names = {a["name"] for a in agents.load_agents()}
    awake_bg = [st["name"] for st in states.values() if st["name"] not in agent_names and st.get("state") != "자는 중"]
    assert "지금 동네는" in out
    assert not awake_bg or any(n in out for n in awake_bg)


def test_decide_all_runs_lanes_in_parallel_and_keeps_order(monkeypatch):
    import threading
    import time as _t
    active = {"own": 0, "base": 0, "max_own": 0, "max_base": 0}
    lock = threading.Lock()
    own_names = {"소라", "김동식", "조이서"}
    monkeypatch.setattr(agents, "agent_model", lambda n: "groq" if n in own_names else "")
    monkeypatch.setattr(agents, "DECIDE_PARALLEL", 1)
    monkeypatch.setattr(agents, "DECIDE_PARALLEL_OWN", 3)

    def fake_decide(agent, st, hint, kakao, others):
        lane = "own" if agent["name"] in own_names else "base"
        with lock:
            active[lane] += 1
            active["max_" + lane] = max(active["max_" + lane], active[lane])
        _t.sleep(0.05)
        with lock:
            active[lane] -= 1
        return {"who": agent["name"]}
    monkeypatch.setattr(agents, "_decide", fake_decide)
    people = [{"name": n} for n in ("애순이", "소라", "오상식", "김동식", "조이서", "이지안")]
    out = agents._decide_all([(p, {}, "", [], "") for p in people], people)
    assert [d["who"] for d in out] == [p["name"] for p in people]  # 결과는 원래 순서대로
    assert active["max_base"] == 1  # 기본 모델(분당 한도가 작은 무료 키)은 한 번에 한 명
    assert active["max_own"] == 3   # 인물 전용 모델은 동시에


def test_persona_and_voice_reach_every_speaking_prompt(fake_llm, monkeypatch):
    _quiet(monkeypatch)
    fake = fake_llm(_handler)
    agents.tick(characters.load_roster())
    decide = [(s, u) for s, u in fake.calls if "이번 한 시간 동안" in u]
    talk = [(s, u) for s, u in fake.calls if "다음 한마디" in u]
    assert decide and talk
    for system, user in decide + talk:
        assert "[연기 원칙]" in system and "[말투]" in system
        assert "[말투 지키기]" in user  # 긴 지각 뒤 맨 끝에도 말투를 다시
    s, u = talk[0]
    me = next(a for a in agents.load_agents() if f"'{a['name']}'" in s)
    assert me["speech"] in u
