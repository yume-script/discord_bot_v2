"""몬스터 침입: 실제 수치로 위험도, 매일 밤 침입(문턱을 올리면 조용한 밤), 방어력 vs 몬스터, 결과/경험치/레벨업 반영."""
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
    assert s2 > score and s2 >= 60
    assert any("결계 부하" in w for w in why2) and any("감시탑 2곳" in w for w in why2) and any("비/눈" in w for w in why2)
    assert raids.level_word(s2) in ("위험", "매우 위험")


def test_resolve_outcomes_by_ratio():
    rnd = random.Random(1)
    items = [{"item": "결계석", "cond": 100}, {"item": "감시탑", "cond": 100}, {"item": "목책", "cond": 0}]
    assert raids.structures_power(items) == 12 + 9                   # 망가진 목책은 0
    strong = [{"name": "베니마루", "power": 70, "asleep": False}, {"name": "카즈마", "power": 60, "asleep": False}]
    r = raids.resolve(20, [], items, strong, 10, rnd)
    assert r["outcome"] == "대승" and r["loot"] > 0 and not r["injured"]
    r = raids.resolve(95, ["x"], [], [{"name": "고브타", "power": 2, "asleep": True}], 0, random.Random(2))
    assert r["outcome"] == "성벽 돌파" and r["injured"] == ["고브타"] and r["broken"] >= 3


def test_quiet_night_only_when_threshold_raised(monkeypatch):
    monkeypatch.setattr(raids, "THRESHOLD", 50)
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
    fake_llm(lambda system, user: {"story": "오크들이 서쪽 성문을 두드렸다. 베니마루가 막아섰다.", "summary": "오크 격퇴"})
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (55, ["결계 부하 90%"]))
    decor.act("베니마루", {"do": "place", "item": "목책", "near": "서쪽 성문"}, datetime(2026, 10, 25, 15))
    fund = decor._state()["fund"]
    sent, posts = [], []
    rec = raids.run(NIGHT, post=posts.append, inject=lambda t, d, title: sent.append(title) or [])
    assert rec["raid"] and rec["monster"] == "늑대 떼" and rec["fighters"]
    assert posts and "몬스터 침입" in posts[0] and "오크들이" in posts[0] and rec["outcome"] in posts[0]
    assert sent == [f"몬스터 침입: 늑대 떼 {rec['outcome']}"]
    assert decor._state()["fund"] == fund + rec["loot"]
    if rec["hit"]:
        assert decor._state()["items"][0]["cond"] < 100
    who = rec["fighters"][0]
    assert any("침입" in m["text"] for m in memory.store.memories(who))
    assert any("몬스터 침입" in r.get("reason", "") for r in life.get(who).get("recent", []))
    assert "최근 몬스터 침입" in raids.prompt_block(datetime(2026, 10, 26, 9))
    assert "늑대 떼" in raids.summary(datetime(2026, 10, 26, 9)) and "전투 레벨" in raids.summary(datetime(2026, 10, 26, 9))
    from poring_food import levels
    assert levels.get(who)["battles"] == 1 and (levels.get(who)["xp"] > 0 or levels.level(who) > 1)
    assert levels.get(rec["mvp"])["mvp"] == 1


def test_forecast_shown_in_afternoon(monkeypatch):
    monkeypatch.setattr(raids, "MODE", "nightly")
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (70, ["불 꺼진 감시탑 1곳"]))
    block = raids.prompt_block(datetime(2026, 10, 25, 15))
    assert "오늘 밤 숲의 기척 - 위험" in block and "감시탑" in block and "오크 전사단급" in block and "방어 시설" in block
    assert "오늘 밤" not in raids.prompt_block(datetime(2026, 10, 25, 9))   # 오전엔 아직 모른다


def test_every_night_by_default(monkeypatch):
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (5, []))
    rec = raids.run(NIGHT, post=lambda t: None, inject=lambda *a: [])
    assert rec["raid"] and rec["monster"] == "포링 떼"          # 잠잠한 밤에도 포링 떼는 온다


def test_levels_gain_titles_and_power():
    from poring_food import levels
    assert levels.level("카즈마") == 1 and levels.multiplier("카즈마") == 1
    ups = levels.gain({"카즈마": levels.need(1) + levels.need(2) + 5}, battle=True, mvp="카즈마")
    assert ups == [{"name": "카즈마", "from": 1, "to": 3, "title": ""}]
    assert levels.get("카즈마") == {"level": 3, "xp": 5, "battles": 1, "mvp": 1, "best": ""}
    assert levels.multiplier("카즈마") > 1 and "Lv.3" in levels.prompt_block("카즈마")
    ups = levels.gain({"카즈마": sum(levels.need(lv) for lv in range(3, 5))})
    assert ups[0]["to"] == 5 and ups[0]["title"] == "숙련" and levels.tag("카즈마") == "Lv.5 숙련"
    assert levels.prompt_block("애순이") == ""                          # 아직 싸운 적 없음


def test_defenders_include_awake_helpers_and_scale_with_level():
    from poring_food import agents, characters, levels
    ag = [{"name": "베니마루", "sleep": [23, 6]}, {"name": "고브타", "sleep": [23, 7]}, {"name": "리리나", "sleep": [21, 7]}]
    f = {d["name"]: d for d in raids.defenders(ag, characters.load_roster(), 22, lambda n: 1.0)}
    assert not f["베니마루"]["support"] and f["고브타"]["support"] and "리리나" not in f   # 자는 비전투원은 안 나옴
    before = f["베니마루"]["power"]
    levels.gain({"베니마루": levels.need(1)})
    assert raids.defenders(ag, characters.load_roster(), 22, lambda n: 1.0)[0]["power"] > before
    _ = agents


def test_surge_detection():
    calm = [["t", 30.0, False]] * 12
    assert raids.surge(calm + [["t", 85.0, False]] * 2, playing=2) == {"peak": 85.0, "base": 30.0, "rise": 55.0, "playing": 2}
    assert raids.surge(calm + [["t", 85.0, False]] * 2, playing=0) is None          # 재생이 없으면 습격 아님
    assert raids.surge([["t", 70.0, False]] * 14, playing=1) is None               # 원래 높았던 건 급등이 아님
    assert raids.surge(calm + [["t", 60.0, False]] * 2, playing=1) is None          # 70% 미만
    assert raids.surge(calm[:4], playing=1) is None                                 # 기록이 모자람
    x = {"date": "d", "surge": {"peak": 90.0, "base": 35.0, "rise": 55.0, "playing": 2}}
    score, why = raids.danger(x)
    assert score > raids.danger({"date": "d"})[0] + 30 and "급등" in why[0]


def test_surge_due_cooldown_and_run(monkeypatch, fake_llm):
    monkeypatch.setattr(raids, "MODE", "surge")
    data = {"hosts": {"plex01": {"playing": 1, "samples": [["t", 30.0, False]] * 12 + [["t", 88.0, False]] * 2}}}
    t = datetime(2026, 10, 31, 15, 0)
    got = raids.surge_due(t, data)
    assert got and got["host"] == "plex01"
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    posts = []
    rec = raids.run(t, post=posts.append, inject=lambda *a: [], surge_info=got)
    assert rec["raid"] and rec["surge"] and "불시 습격" in posts[0] and "30%→88%" in posts[0]
    assert raids.surge_due(t.replace(hour=16), data) is None                       # 쿨다운 (180분)
    t2 = t.replace(hour=19)
    rec2 = raids.run(t2, post=posts.append, inject=lambda *a: [], surge_info=raids.surge_due(t2, data))
    assert rec2 and len([r for r in raids._load()["days"].values() if r.get("raid")]) == 2   # 같은 날 두 번째 습격도 따로 기록
    assert not raids.should_run(datetime(2026, 11, 1, raids.RAID_HOUR))            # surge 모드에선 밤 고정 침입 없음
    assert "불시" in raids.summary(t2)
