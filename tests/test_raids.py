"""장애 비상: 실제 수치로 위험도, 매일 밤 장애 비상(문턱을 올리면 조용한 밤), 대응력 vs 몬스터, 결과/경험치/레벨업 반영."""
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
    assert any("서버 부하" in w for w in why2) and any("모니터링 2곳" in w for w in why2) and any("비가 와서" in w for w in why2)
    assert raids.level_word(s2) in ("위험", "매우 위험")


def test_resolve_outcomes_by_ratio():
    rnd = random.Random(1)
    items = [{"item": "메인 서버", "cond": 100}, {"item": "모니터링", "cond": 100}, {"item": "방화벽", "cond": 0}]
    assert raids.structures_power(items) == 12 + 9                   # 망가진 방화벽은 0
    strong = [{"name": "권태준", "power": 70, "asleep": False}, {"name": "민재현", "power": 60, "asleep": False}]
    r = raids.resolve(20, [], items, strong, 10, rnd)
    assert r["outcome"] == "완벽 방어" and r["loot"] > 0 and not r["injured"]
    r = raids.resolve(95, ["x"], [], [{"name": "고민수", "power": 2, "asleep": True}], 0, random.Random(2))
    assert r["outcome"] == "서비스 중단" and r["injured"] == ["고민수"] and r["broken"] >= 3


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
    fake_llm(lambda system, user: {"story": "오크들이 서쪽 출입구를 두드렸다. 권태준이 막아섰다.", "summary": "오크 복구 성공"})
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (55, ["서버 부하 90%"]))
    decor.act("권태준", {"do": "place", "item": "방화벽", "near": "서쪽 출입구"}, datetime(2026, 10, 25, 15))
    fund = decor._state()["fund"]
    sent, posts = [], []
    rec = raids.run(NIGHT, post=posts.append, inject=lambda t, d, title: sent.append(title) or [])
    assert rec["raid"] and rec["monster"] == "동시 접속 폭주" and rec["fighters"]
    assert posts and "장애 비상" in posts[0] and "오크들이" in posts[0] and rec["outcome"] in posts[0]
    assert sent == [f"장애 비상: 동시 접속 폭주 {rec['outcome']}"]
    assert decor._state()["fund"] == fund + rec["loot"]
    if rec["hit"]:
        assert decor._state()["items"][0]["cond"] < 100
    who = rec["fighters"][0]
    assert any("장애 비상" in m["text"] for m in memory.store.memories(who))
    assert any("장애 비상" in r.get("reason", "") for r in life.get(who).get("recent", []))
    assert "최근 장애 비상" in raids.prompt_block(datetime(2026, 10, 26, 9))
    assert "동시 접속 폭주" in raids.summary(datetime(2026, 10, 26, 9)) and "경력 레벨" in raids.summary(datetime(2026, 10, 26, 9))
    from poring_food import levels
    assert levels.get(who)["battles"] == 1 and (levels.get(who)["xp"] > 0 or levels.level(who) > 1)
    assert levels.get(rec["mvp"])["mvp"] == 1


def test_forecast_shown_in_afternoon(monkeypatch):
    monkeypatch.setattr(raids, "MODE", "nightly")
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (70, ["불 꺼진 모니터링 1곳"]))
    block = raids.prompt_block(datetime(2026, 10, 25, 15))
    assert "오늘 밤 트래픽 기상도 - 위험" in block and "모니터링" in block and "연쇄 마감 사고급" in block and "설비" in block
    assert "오늘 밤" not in raids.prompt_block(datetime(2026, 10, 25, 9))   # 오전엔 아직 모른다


def test_every_night_by_default(monkeypatch):
    monkeypatch.setattr(raids, "gather", lambda now=None: {"date": "x"})
    monkeypatch.setattr(raids, "danger", lambda x: (5, []))
    rec = raids.run(NIGHT, post=lambda t: None, inject=lambda *a: [])
    assert rec["raid"] and rec["monster"] == "문의 폭주"          # 잠잠한 밤에도 문의 폭주는 온다


def test_levels_gain_titles_and_power():
    from poring_food import levels
    assert levels.level("민재현") == 1 and levels.multiplier("민재현") == 1
    ups = levels.gain({"민재현": levels.need(1) + levels.need(2) + 5}, battle=True, mvp="민재현")
    assert ups == [{"name": "민재현", "from": 1, "to": 3, "title": ""}]
    assert levels.get("민재현") == {"level": 3, "xp": 5, "battles": 1, "mvp": 1, "best": ""}
    assert levels.multiplier("민재현") > 1 and "Lv.3" in levels.prompt_block("민재현")
    ups = levels.gain({"민재현": sum(levels.need(lv) for lv in range(3, 5))})
    assert ups[0]["to"] == 5 and ups[0]["title"] == "주임" and levels.tag("민재현") == "Lv.5 주임"
    assert levels.prompt_block("애순이") == ""                          # 아직 싸운 적 없음


def test_defenders_include_awake_helpers_and_scale_with_level():
    from poring_food import agents, characters, levels
    ag = [{"name": "권태준", "sleep": [23, 6]}, {"name": "고민수", "sleep": [23, 7]}, {"name": "유리나", "sleep": [21, 7]}]
    f = {d["name"]: d for d in raids.defenders(ag, characters.load_roster(), 22, lambda n: 1.0)}
    assert not f["권태준"]["support"] and f["고민수"]["support"] and "유리나" not in f   # 자는 비전투원은 안 나옴
    before = f["권태준"]["power"]
    levels.gain({"권태준": levels.need(1)})
    assert raids.defenders(ag, characters.load_roster(), 22, lambda n: 1.0)[0]["power"] > before
    _ = agents


def test_surge_detection():
    calm = [["t", 30.0, False]] * 12
    assert raids.surge(calm + [["t", 85.0, False]] * 2, playing=2) == {"peak": 85.0, "base": 30.0, "rise": 55.0, "playing": 2}
    assert raids.surge(calm + [["t", 85.0, False]] * 2, playing=0) is None          # 재생이 없으면 비상 호출 아님
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
    assert rec["raid"] and rec["surge"] and "불시 비상 호출" in posts[0] and "30%→88%" in posts[0]
    assert raids.surge_due(t.replace(hour=16), data) is None                       # 쿨다운 (180분)
    t2 = t.replace(hour=19)
    rec2 = raids.run(t2, post=posts.append, inject=lambda *a: [], surge_info=raids.surge_due(t2, data))
    assert rec2 and len([r for r in raids._load()["days"].values() if r.get("raid")]) == 2   # 같은 날 두 번째 비상 호출도 따로 기록
    assert not raids.should_run(datetime(2026, 11, 1, raids.RAID_HOUR))            # surge 모드에선 밤 고정 장애 비상 없음
    assert "불시" in raids.summary(t2)
