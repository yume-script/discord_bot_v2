"""담당 실적(KPI): 자리 배정/인수인계, 점수 기록, 주간 순위 발표와 상벌, 날씨 장사."""
from datetime import datetime

from poring_food import agents, characters, kpi, life


def _agents(without=()):
    return [a for a in agents.load_agents() if a["name"] not in without]


def test_assign_first_holders_then_handover():
    roster = characters.load_roster()
    posts, changes = kpi.assign(_agents(), roster)
    assert not changes  # 처음 배정은 인수인계가 아니다
    assert [p["id"] for p in posts["애순이"]] == ["books"]
    assert [p["id"] for p in posts["권민우"]] == ["sales"]
    assert [p["id"] for p in posts["리정혁"]] == ["facility_stability"]
    # 권민우가 쉬러 가면 같은 영업기획부 장대희가 출하를 맡는다
    posts, changes = kpi.assign(_agents(without=("권민우",)), roster)
    assert [(p["id"], o, n) for p, o, n in changes] == [("sales", "권민우", "장대희")]
    assert "sales" in [p["id"] for p in posts["장대희"]]
    assert kpi.posts_of("장대희")[0]["id"] == "sales"
    # 권민우가 돌아와도 지금 담당자(장대희)가 계속 맡는다
    _, changes = kpi.assign(_agents(), roster)
    assert not changes


def test_rotation_no_longer_fixes_duty_holders():
    from poring_food import rotation
    kwon = next(a for a in agents.load_agents() if a["name"] == "김동식")
    assert not rotation._fixed(kwon)


def test_record_scores(monkeypatch):
    kpi.assign(_agents(), characters.load_roster())
    counts = {"books": {"today": 20, "avg": 10.0, "saturated": False, "pace": "평소보다 훨씬 바쁨"},
              "sales": {"today": 5, "avg": 10.0, "saturated": False, "pace": "평소보다 한산함"}}
    monkeypatch.setattr(kpi.metrics, "count", lambda k: counts.get(k))
    monkeypatch.setattr(kpi.metrics, "streams", lambda: 2)
    monkeypatch.setattr(kpi.signals, "live_value", lambda k: {"cpu": 40.0, "disk": 70.0, "uptime_days": 0.5, "down": 1})
    now = datetime(2026, 10, 7, 15)
    kpi.record(now)
    rows = kpi._load()["daily"]["2026-10-07"]
    assert rows["books"]["score"] == 200 and rows["books"]["holder"] == "애순이"
    assert rows["sales"]["score"] == 50 and rows["sales"]["holder"] == "권민우"
    assert rows["facility_disk"]["score"] == 80
    assert rows["facility_stability"]["score"] == 30  # 멈춘 서비스 1개 + 재가동
    assert rows["streams"]["holder"] == "소라"
    block = kpi.prompt_block("이지안", kpi.posts_of("이지안"))
    assert "창고 적재율 70%" in block


def _seed_week(scores: dict[str, tuple[str, int]]):
    data = kpi._load()
    data["daily"] = {f"2026-10-{d:02d}": {pid: {"holder": who, "score": sc, "company": kpi.POST_BY_ID[pid]["company"],
                                                "sum": 1, "n": 1, "last": 1}
                                          for who, (pid, sc) in scores.items()} for d in range(5, 12)}
    kpi._save(data)


def test_weekly_ranking_and_rewards(fake_llm, monkeypatch):
    agent_list = _agents()
    roster = characters.load_roster()
    kpi.assign(agent_list, roster)
    _seed_week({"애순이": ("books", 150), "권민우": ("sales", 90), "최수연": ("orders", 120),
                "리정혁": ("facility_stability", 100)})
    sent = []
    monkeypatch.setattr(agents.runtime, "send_as", lambda acc, name, text, *a, **k: sent.append((name, text)))
    monkeypatch.setattr(agents, "_group_channel", lambda g: 1)
    fake_llm(lambda system, user: {"text": "지난주 1등 애순이! 권민우는 분발."} if "주간 실적" in user else {})
    money = life.get("애순이")["money"]
    data = {}
    monday = datetime(2026, 10, 12, 9, 30)
    assert kpi.weekly_due(monday)
    agents._kpi_reviews(agent_list, roster, data, monday)
    assert not kpi.weekly_due(monday)
    assert life.get("애순이")["money"] == money + kpi.WEEKLY_BONUS
    assert any("3명 중 1등" in m["text"] for m in data["애순이"]["inbox"])
    assert any("3명 중 3등" in m["text"] for m in data["권민우"]["inbox"])
    assert any("실적 100점" in m["text"] for m in data["리정혁"]["inbox"])  # 에린은 혼자라 본인에게만
    assert sent and sent[0][0] == "정명석" and "애순이" in sent[0][1]
    # 같은 주에 다시 돌아도 또 발표하지 않는다
    agents._kpi_reviews(agent_list, roster, {}, monday.replace(hour=11))
    assert len(sent) == 1


def test_monthly_title_is_permanent(monkeypatch):
    from poring_food import memory
    agent_list = _agents()
    roster = characters.load_roster()
    kpi.assign(agent_list, roster)
    data = kpi._load()
    data["daily"] = {"2026-09-10": {"books": {"holder": "애순이", "score": 180, "company": kpi.PF},
                                    "sales": {"holder": "권민우", "score": 80, "company": kpi.PF}}}
    kpi._save(data)
    monkeypatch.setattr(agents, "_group_channel", lambda g: 0)
    agents._kpi_reviews(agent_list, roster, {}, datetime(2026, 10, 1, 10))
    assert kpi.titles("애순이") == ["2026년 9월 우수 사원"]
    assert any(m.get("permanent") and "9월 우수 사원" in m["text"] for m in memory.store.memories("애순이"))
    assert not kpi.monthly_due(datetime(2026, 10, 1, 11))


def test_weather_trade():
    rain = "기온 18°C, 흐림, 비 (1mm), 습도 80% (기상청 실측 예보)"
    hot = "기온 33°C, 맑음, 습도 50% → 폭염 - 현장 더위 비상 (기상청 실측 예보)"
    assert kpi.weather_trade("카페", rain)["pct"] == 15
    assert kpi.weather_trade("포장마차", rain)["pct"] == 25
    assert kpi.weather_trade("카페", hot)["pct"] == 20  # "비상"을 비로 읽지 않는다
    assert kpi.weather_trade("포장마차", hot)["pct"] == -20
    assert kpi.weather_trade("포장마차", "기온 15°C, 맑음")["pct"] == 0
    assert kpi.weather_trade("", rain) is None
    assert kpi.weather_trade("카페", "") is None


def test_facility_numbers():
    from poring_food import mcp_signals
    status = {"cpu": {"host_percent": 42.5}, "memory": {"used_percent": 60}, "uptime": "3 days, 1:00:00",
              "disks": [{"used_percent": 55}, {"used_percent": 81}], "temperature_c": {"max": 95}}
    services = {"a": {"state": "active"}, "b": {"state": "failed"}}
    out = mcp_signals.facility_numbers(status, services)
    assert out["cpu"] == 42.5 and out["disk"] == 81 and out["down"] == 1 and int(out["uptime_days"]) == 3
    assert mcp_signals.facility_numbers({}, None) == {}
