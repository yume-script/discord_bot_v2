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
    monkeypatch.setattr(agents, "TOWN_MAP", False)  # 글 버전 (그림은 test_town_map_png에서)
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


def test_ripple_round_chains_reactions_within_the_hour(fake_llm, monkeypatch, caplog):
    """홍두식-윤혜진 대화 -> (반응) 윤혜진이 표미선에게 하소연 -> (다음 바퀴) 표미선이 동네 단톡방에 한마디. 같은 시간 안에."""
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    monkeypatch.setattr(agents, "RIPPLE_CONVOS_PER_HOUR", 2)
    first = {"홍두식": "윤혜진"}

    def handler(system, user):
        me = re.search(r"'([^']+)'", system).group(1)
        if "이번 한 시간 동안" in user:
            c = {"to": first[me], "how": "직접", "opening": "혜진 쌤!"} if me in first else None
            return {"location": "카페 오후세시", "activity": "일", "state": "일하는 중", "thought": "흠", "contact": c}
        if "다음 한마디" in user:
            return {"say": "응", "end": True}
        if "방금 이번 시간에 일어난 일" in user:
            if me == "윤혜진":
                return {"react": "연락", "thought": "미선이한테 얘기해야지", "matters": 7, "consider": "속상해서 못 참겠다",
                        "contact": {"to": "표미선", "how": "메신저", "opening": "미선아 있잖아"}}
            if me == "표미선":
                return {"react": "단톡방", "thought": "다들 알아야 해",
                        "group_post": {"group": "동네 단톡방", "text": "여화정 통장님 이것 좀 보세요"}}
            return {"react": "가만히", "thought": "그렇구나"}
        return {}
    fake_llm(handler)
    monkeypatch.setattr(agents, "_group_channel", lambda g: 1)
    monkeypatch.setattr(agents.runtime, "has_agent_sender", lambda: False)
    monkeypatch.setattr(agents.runtime, "send_as", lambda *a, **k: None)
    agents.tick(characters.load_roster())
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert re.search(r"대화 \d+마디: 홍두식[와과] 윤혜진", log)
    assert "파급 1바퀴" in log and "윤혜진" in log
    assert re.search(r"대화 \d+마디: 윤혜진과 표미선", log)          # 반응이 새 대화로
    assert "동네 단톡방 표미선: 여화정 통장님 이것 좀 보세요" in log    # 그 대화가 또 다음 바퀴 반응으로
    assert "파급 3바퀴" in log or "파급 2바퀴" in log
    m = re.search(r"파급 라운드: 반응 고민 (\d+)번, 행동 (\d+)번", log)
    assert m and int(m.group(1)) <= agents.RIPPLE_MAX_REACTIONS and int(m.group(2)) >= 2
    assert "고민:" in log  # 반응할지 말지 따져 본 고민이 남는다
    # 같은 시간에 다시 돌아도 반응/대화가 또 생기지 않는다
    caplog.clear()
    agents.tick(characters.load_roster())
    assert "파급" not in "\n".join(r.getMessage() for r in caplog.records)


def test_ripple_respects_caps(fake_llm, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    monkeypatch.setattr(agents, "RIPPLE_MAX_REACTIONS", 3)

    def handler(system, user):
        me = re.search(r"'([^']+)'", system).group(1)
        if "이번 한 시간 동안" in user:
            c = {"to": PAIRS[me], "how": "직접", "opening": "야"} if me in PAIRS else None
            return {"location": "골목", "activity": "일", "state": "일하는 중", "thought": "흠", "contact": c}
        if "다음 한마디" in user:
            return {"say": "응", "end": True}
        if "방금 이번 시간에 일어난 일" in user:
            return {"react": "연락", "thought": "또 연락", "contact": {"to": "여화정", "how": "메신저", "opening": "통장님"}}
        return {}
    fake_llm(handler)
    agents.tick(characters.load_roster())
    log = "\n".join(r.getMessage() for r in caplog.records)
    m = re.search(r"파급 라운드: 반응 고민 (\d+)번, 행동 \d+번, 새 대화 (\d+)개", log)
    assert m and int(m.group(1)) <= 3 and int(m.group(2)) <= agents.RIPPLE_CONVOS_PER_HOUR
    assert agents.RIPPLE_PER_PERSON == 5 and agents.RIPPLE_CONVOS_PER_HOUR == 5


def test_ripple_agent_decides_whether_to_react(fake_llm, monkeypatch, caplog):
    """스스로 매긴 중요도가 낮으면 '연락'이라고 해도 가만히 / 단톡방 글은 이름 불린 사람 말고 본 사람도 고민해 본다."""
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    monkeypatch.setattr(agents, "_group_channel", lambda g: 1)
    monkeypatch.setattr(agents.runtime, "has_agent_sender", lambda: False)

    def handler(system, user):
        me = re.search(r"'([^']+)'", system).group(1)
        if "이번 한 시간 동안" in user:
            gp = {"group": "동네 단톡방", "text": "오늘 골목 시끄럽네요"} if me == "부상길" else None
            c = {"to": "윤혜진", "how": "직접", "opening": "쌤"} if me == "홍두식" else None
            return {"location": "골목", "activity": "일", "state": "일하는 중", "thought": "흠", "contact": c, "group_post": gp}
        if "다음 한마디" in user:
            return {"say": "응", "end": True}
        if "방금 이번 시간에 일어난 일" in user:
            return {"consider": "별로 상관없는 일", "matters": 2, "react": "연락",
                    "contact": {"to": "여화정", "how": "메신저", "opening": "통장님"}, "thought": "그냥 넘기자"}
        return {}
    fake_llm(handler)
    agents.tick(characters.load_roster())
    log = "\n".join(r.getMessage() for r in caplog.records)
    assert "반응 윤혜진: 가만히 (중요도 2) - 고민: 별로 상관없는 일" in log
    m = re.search(r"파급 라운드: 반응 고민 (\d+)번, 행동 (\d+)번, 새 대화 (\d+)개", log)
    assert m and m.group(2) == "0" and m.group(3) == "0"
    first = re.search(r"파급 1바퀴 \([^)]*\): (.+)", log).group(1).split(", ")
    assert len(set(first) - {"홍두식", "윤혜진"}) >= 1  # 단톡방 글을 본 사람도 반응할지 고민해 본다


def test_town_map_png(fake_llm, monkeypatch, tmp_path):
    """매시 동네 지도 그림: 장소별 사람 + 이번 시간 대화 선. 글꼴을 못 구하는 환경이면 건너뛴다."""
    import pytest
    from poring_food import outbox, townmap
    if not townmap._font_file():
        pytest.skip("한글 글꼴 없음")
    monkeypatch.setattr(townmap, "MAP_DIR", str(tmp_path))
    _quiet(monkeypatch)
    monkeypatch.setattr(outbox, "paced", lambda: True)
    fake_llm(_handler)
    agents.tick(characters.load_roster())
    states = characters.update_all_states(characters.load_roster(), random.Random(1))
    snap = agents._town_snapshot(states, agents.names(), set(), agents.now_kst(), 3)
    assert snap["zones"] and snap["talks"]                       # 이번 시간 대화가 선으로 들어간다
    agents.post_town_view(states)
    items = outbox.take_due(agents.now_kst() + agents.timedelta(minutes=80), limit=500)
    files = [it for it in items if it["kind"] == "file"]
    assert len(files) == 1 and files[0]["path"].endswith(".png")
    from PIL import Image
    im = Image.open(files[0]["path"])
    assert im.size[0] == townmap.W and im.size[1] > 400
    assert not [it for it in items if it["kind"] == "agent" and it["name"] == "동네 소식" and "지금 동네는" in it["text"]]


def test_daily_cap_counts_only_first_decision_convos(fake_llm, monkeypatch, caplog):
    """예전 기록(반응 대화까지 하루 상한에 섞여 48개가 참)이어도 처음 판단 대화만 다시 세서 대화가 계속 생긴다."""
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    fake_llm(_handler)
    now = agents.now_kst()
    data = agents._load_state()
    data["_convos"] = {"date": now.strftime("%Y-%m-%d"), "count": agents.MAX_CONVOS_PER_DAY,
                       "hours": [], "per_hour": {now.strftime("%Y-%m-%d") + " 01": 20}}
    agents._save_state(data)
    agents.tick(characters.load_roster())
    assert len(_talks(caplog)) == agents.MAX_CONVOS_PER_HOUR
    convo = agents._load_state()["_convos"]
    assert convo["count"] == 20 + agents.MAX_CONVOS_PER_HOUR and "ripple" in convo


def test_agent_plays_bot_game_in_free_time(fake_llm, monkeypatch, caplog):
    """판단에서 game을 고르면 봇 미니게임을 실제로 한 판 하고 이야기 속 돈이 바뀐다."""
    from poring_food import life
    caplog.set_level(logging.INFO, logger="poring_food")
    _quiet(monkeypatch)
    seen = {}

    def handler(system, user):
        out = _handler(system, user)
        if "이번 한 시간 동안" in user and "'홍두식'" in system:
            seen["rule"] = "봇 오락실" in user
            out.update(state="개인시간", contact=None, game={"name": "주사위", "bet": 5000})
        return out

    fake_llm(handler)
    before = int(life.get("홍두식")["money"])
    agents.tick(characters.load_roster())
    assert seen["rule"]
    assert any("[오락실] 홍두식 /주사위 5000" in r.getMessage() for r in caplog.records)
    assert int(life.get("홍두식")["money"]) != before or any("+0원" in r.getMessage() for r in caplog.records)
