"""몬스터 침입: 실제 수치로 위험도, 문턱을 넘는 밤에만 침입, 방어력(시설+수비대+민병+사기) vs 몬스터, 결과 반영."""
import random
from datetime import datetime

from poring_food import decor, raids

NIGHT = datetime(2026, 10, 25, 22, 3)


def test_danger_uses_real_numbers_and_luck_is_fixed_per_day():
    calm = {"cpu": 10, "down": 0, "uptime_days": 30, "towers_down": 0, "weather": "", "date": "2026-10-25"}
    score, why = raids.danger(calm)
    assert raids.danger(calm) == (score, why)                       # 같은 날은 같은 값
    bad = dict(calm, cpu=90, down=1, uptime_days=0.2, towers_down=2, weather="rain")
    s2, why2 = raids.danger(bad)
    assert s2 > score and s2 >= raids.THRESHOLD
    assert any("결계 부하" in w for w in why2) and any("감시탑 2곳" in w for w in why2) and any("비/눈" in w for w in why2)
    assert raids.level_word(s2) in ("위험", "매우 위험")


def test_resolve_outcomes_by_ratio():
    rnd = random.Random(1)
    items = [{"item": "결계석", "cond": 100}, {"item": "감시탑", "cond": 100}, {"item": "목책", "cond": 0}]
    assert raids.structures_power(items) == 12 + 9                   # 망가진 목책은 0
    strong = [{"name": "리정혁", "power": 40, "asleep": False}, {"name": "홍두식", "power": 30, "asleep": False}]
    r = raids.resolve(55, [], items, strong, 10, rnd)
    assert r["outcome"] == "대승" and r["loot"] > 0 and not r["injured"]
    r = raids.resolve(95, ["x"], [], [{"name": "최택", "power": 2, "asleep": True}], 0, random.Random(2))
    assert r["outcome"] == "성벽 돌파" and r["injured"] == ["최택"] and r["broken"] >= 3


def test_quiet_night_records_only(monkeypatch):
    monkeypatch.setattr(raids, "gather", lambda now=None: {"cpu": 0, "date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (20, []))
    posts = []
    rec = raids.run(NIGHT, post=posts.append, inject=lambda *a: [])
    assert rec["raid"] is False and "조용한 밤" in posts[0]
    assert raids.done_today(NIGHT) and not raids.should_run(NIGHT)
    assert raids.run(NIGHT, post=posts.append) is None              # 하루 한 번
    assert "조용한 밤" in raids.prompt_block(datetime(2026, 10, 26, 9))


def test_raid_damages_board_pays_loot_and_remembers(monkeypatch, fake_llm):
    from poring_food import life, memory
    fake_llm(lambda system, user: {"story": "오크들이 서쪽 성문을 두드렸다. 리정혁이 막아섰다.", "summary": "오크 격퇴"})
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (75, ["결계 부하 90%"]))
    decor.act("리정혁", {"do": "place", "item": "목책", "near": "서쪽 성문"}, datetime(2026, 10, 25, 15))
    fund = decor._state()["fund"]
    sent, posts = [], []
    rec = raids.run(NIGHT, post=posts.append, inject=lambda t, d, title: sent.append(title) or [])
    assert rec["raid"] and rec["monster"] == "오크 전사단" and rec["fighters"]
    assert posts and "몬스터 침입" in posts[0] and "오크들이" in posts[0] and rec["outcome"] in posts[0]
    assert sent == [f"몬스터 침입: 오크 전사단 {rec['outcome']}"]
    assert decor._state()["fund"] == fund + rec["loot"]
    if rec["hit"]:
        assert decor._state()["items"][0]["cond"] < 100
    who = rec["fighters"][0]
    assert any("침입" in m["text"] for m in memory.store.memories(who))
    assert any("몬스터 침입" in r.get("reason", "") for r in life.get(who).get("recent", []))
    assert "지난밤 몬스터 침입" in raids.prompt_block(datetime(2026, 10, 26, 9))
    assert "오크 전사단" in raids.summary(datetime(2026, 10, 26, 9))


def test_forecast_shown_in_afternoon(monkeypatch):
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (70, ["불 꺼진 감시탑 1곳"]))
    block = raids.prompt_block(datetime(2026, 10, 25, 15))
    assert "오늘 밤 숲의 기척 - 위험" in block and "감시탑" in block and "방어 시설" in block
    assert "오늘 밤" not in raids.prompt_block(datetime(2026, 10, 25, 9))   # 오전엔 아직 모른다
