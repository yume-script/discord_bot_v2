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
    assert [p["id"] for p in posts["성덕선"]] == ["sales"]
    assert [p["id"] for p in posts["리정혁"]] == ["facility_stability"]
    assert [p["id"] for p in posts["소라"]] == ["streams"]
    # 성덕선이 쉬러 가면 같은 별빛 환영극장 매표·홍보의 조이서가 매표를 맡는다
    posts, changes = kpi.assign(_agents(without=("성덕선",)), roster)
    assert [(p["id"], o, n) for p, o, n in changes] == [("sales", "성덕선", "조이서")]
    assert "sales" in [p["id"] for p in posts["조이서"]]
    assert kpi.posts_of("조이서")[0]["id"] == "sales"
    # 성덕선이 돌아와도 지금 담당자(조이서)가 계속 맡는다
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
    assert rows["sales"]["score"] == 50 and rows["sales"]["holder"] == "성덕선"
    assert rows["facility_disk"]["score"] == 80
    assert rows["facility_stability"]["score"] == 30  # 멈춘 서비스 1개 + 재가동
    assert rows["streams"]["holder"] == "소라"
    block = kpi.prompt_block("황용식", kpi.posts_of("황용식"))
    assert "창고 적재율 70%" in block
    assert rows["orders"]["holder"] == "최수연" and "score" in rows["orders"]


def test_desk_virtual_inquiries_and_vip(monkeypatch):
    """문의 창구: 가상 문의가 시간대별로 들어오고, 일하면 처리/쉬면 대기로 쌓인다. 실제 사람 메시지는 VIP 가산."""
    monkeypatch.setattr(kpi.signals, "weather_text", lambda: "기온 15°C, 비 (3mm)")
    real = {"n": 0}
    monkeypatch.setattr(kpi.metrics, "orders", lambda: real["n"])
    demand, reasons = kpi.desk_demand(datetime(2026, 10, 12, 9))  # 월요일 + 비
    assert demand == kpi.desk_demand(datetime(2026, 10, 12, 20))[0]  # 같은 날은 같은 수요
    assert any("월요일" in r for r in reasons) and any("비" in r for r in reasons)
    data = {}
    # 밤새 아무도 안 받으면 대기로 쌓인다
    for h in range(1, 9):
        kpi._desk_step(data, datetime(2026, 10, 12, h), {"state": "자는 중"})
    night = data["desk"]["backlog"]
    assert night == data["desk"]["arrived"] > 0 and data["desk"]["handled"] == 0
    # 출근해서 일하면 밀린 것까지 처리
    for h in range(9, 13):
        kpi._desk_step(data, datetime(2026, 10, 12, h), {"state": "일하는 중", "energy": 0.8, "stress": 0.2})
    assert data["desk"]["handled"] >= night and data["desk"]["backlog"] < night + 5
    x = kpi._desk_reading(data["desk"])
    score_before = x["score"]
    assert "손님 문의" in x["text"] and "대기" in x["text"]
    # 실제 사람이 봇에게 2건 보내면 VIP 2건 - 점수 가산 (같은 시간에 다시 돌려도 문의가 또 들어오지 않는다)
    real["n"] = 2
    arrived = data["desk"]["arrived"]
    kpi._desk_step(data, datetime(2026, 10, 12, 12, 30), {"state": "일하는 중"})
    assert data["desk"]["arrived"] == arrived and data["desk"]["vip"] == 2
    x = kpi._desk_reading(data["desk"])
    assert x["score"] > score_before and "VIP" in x["text"]
    # 공석이면 처리 0, 대기가 많으면 경고
    data["desk"]["backlog"] = 40
    kpi._desk_step(data, datetime(2026, 10, 12, 14), None)
    assert kpi._desk_reading(data["desk"])["alert"]
    # 다음 날: 오늘 수치는 새로, 대기는 넘어간다
    left = data["desk"]["backlog"]
    real["n"] = 0
    kpi._desk_step(data, datetime(2026, 10, 13, 0, 5), None)
    assert data["desk"]["date"] == "2026-10-13" and data["desk"]["vip"] == 0
    assert data["desk"]["backlog"] >= left


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
    _seed_week({"애순이": ("books", 150), "김동식": ("books_adult", 90), "최수연": ("orders", 120),
                "성덕선": ("sales", 100)})
    sent = []
    monkeypatch.setattr(agents.runtime, "send_as", lambda acc, name, text, *a, **k: sent.append((name, text)))
    monkeypatch.setattr(agents.runtime, "has_agent_sender", lambda: True)
    monkeypatch.setattr(agents, "_group_channel", lambda g: 1)
    fake_llm(lambda system, user: {"text": "지난주 1등 애순이! 김동식 씨는 분발."} if "도서관 통신방" in user else {})
    money = life.get("애순이")["money"]
    data = {}
    monday = datetime(2026, 10, 12, 9, 30)
    assert kpi.weekly_due(monday)
    agents._kpi_reviews(agent_list, roster, data, monday)
    assert not kpi.weekly_due(monday)
    assert life.get("애순이")["money"] == money + kpi.WEEKLY_BONUS
    assert any("3명 중 1등" in m["text"] for m in data["애순이"]["inbox"])
    assert any("3명 중 3등" in m["text"] for m in data["김동식"]["inbox"])
    assert any("실적 100점" in m["text"] for m in data["성덕선"]["inbox"])  # 극장은 혼자라 본인에게만
    assert sent and sent[0][0] == "애순이" and "애순이" in sent[0][1]   # 도서관 사장이 도서관 통신방에 발표
    # 같은 주에 다시 돌아도 또 발표하지 않는다
    agents._kpi_reviews(agent_list, roster, {}, monday.replace(hour=11))
    assert len(sent) == 1


def test_monthly_title_is_permanent(monkeypatch):
    from poring_food import memory
    agent_list = _agents()
    roster = characters.load_roster()
    kpi.assign(agent_list, roster)
    data = kpi._load()
    data["daily"] = {"2026-09-10": {"books": {"holder": "애순이", "score": 180, "company": kpi.BOOK},
                                    "books_adult": {"holder": "김동식", "score": 80, "company": kpi.BOOK}}}
    kpi._save(data)
    monkeypatch.setattr(agents, "_group_channel", lambda g: 0)
    agents._kpi_reviews(agent_list, roster, {}, datetime(2026, 10, 1, 10))
    assert kpi.titles("애순이") == ["2026년 9월의 공로자"]
    assert any(m.get("permanent") and "9월의 공로자" in m["text"] for m in memory.store.memories("애순이"))
    assert not kpi.monthly_due(datetime(2026, 10, 1, 11))


def test_weather_trade():
    rain = "기온 18°C, 흐림, 비 (1mm), 습도 80% (기상청 실측 예보)"
    hot = "기온 33°C, 맑음, 습도 50% → 폭염 - 현장 더위 비상 (기상청 실측 예보)"
    assert kpi.weather_trade("카페", rain)["pct"] == 15
    assert kpi.weather_trade("선술집", rain)["pct"] == 25
    assert kpi.weather_trade("카페", hot)["pct"] == 20  # "비상"을 비로 읽지 않는다
    assert kpi.weather_trade("선술집", hot)["pct"] == -20
    assert kpi.weather_trade("선술집", "기온 15°C, 맑음")["pct"] == 0
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
