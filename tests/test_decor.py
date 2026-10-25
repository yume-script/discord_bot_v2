"""마을 방어 배치 판: 놓을 수 있는 인물만, 장소 근처 빈칸에 세우기/고치기/치우기, 기금, 매일 닳기와 비, 판 그림."""
from datetime import datetime

import pytest

from poring_food import decor


def test_roles_and_parse():
    assert decor.prompt_rule("카즈마") and "방어 기금" in decor.prompt_rule("카즈마")
    assert decor.prompt_rule("애순이") == ""                                   # 놓는 사람이 아니면 규칙 없음
    assert decor.parse({"do": "place", "item": "약초밭", "near": "오아시스 마법도서관 앞"}, "카즈마")["near"] == "오아시스 마법도서관"
    assert decor.parse({"do": "place", "item": "룬 벽화", "near": "길드 주점"}, "카즈마") is None     # 룬 벽화는 쿠로베만
    assert decor.parse({"do": "place", "item": "룬 벽화", "near": "길드 주점"}, "쿠로베")["item"] == "룬 벽화"
    assert decor.parse({"do": "place", "item": "약초밭"}, "알다프") is None                  # 영주는 치우기만
    assert decor.parse({"do": "place", "item": "분수대"}, "리그루드") is None


def test_place_repair_remove_and_fund():
    now = datetime(2026, 10, 16, 15, 0)
    r = decor.act("카즈마", {"do": "place", "item": "약초밭", "near": "오아시스 마법도서관"}, now)
    assert r["text"] == "오아시스 마법도서관 근처에 약초밭을 세웠다" and r["wage"] == decor.WAGE
    assert r["fund"] == decor.START_FUND - 30000 - decor.WAGE
    item = decor._load()["items"][0]
    assert not decor._blocked(item["x"], item["y"]) and decor._near_name(item["x"], item["y"]) == "오아시스 마법도서관"
    assert decor.act("카즈마", {"do": "place", "item": "목책", "near": "별빛 환영극장"}, now) is None     # 하루 한 번
    # 여러 날 닳으면 낡음 -> 사건, 비 오면 약초밭은 살아난다
    events = []
    decor.daily(datetime(2026, 10, 16, 6), "", inject=lambda *a: events.append(a))          # 첫날은 기준만
    for d in range(17, 26):
        decor.daily(datetime(2026, 10, d, 6), "기온 15°C, 맑음", inject=lambda *a: events.append(a))
    it = decor._load()["items"][0]
    assert it["cond"] < decor.BAD and any("시들어 간다" in e[2] for e in events)
    cond = it["cond"]
    decor.daily(datetime(2026, 10, 26, 6), "기온 12°C, 비 (3mm)")
    assert decor._load()["items"][0]["cond"] == min(100, max(0, cond - 8) + 12)
    decor.daily(datetime(2026, 10, 27, 6), "기온 30°C → 폭염 비상")                        # "비상"은 비가 아니다
    assert decor._load()["items"][0]["cond"] == max(0, cond - 8) + 12 - 8
    # 촌장이 고치고, 영주가 치운다
    r = decor.act("리그루드", {"do": "repair", "item": "약초밭", "near": ""}, datetime(2026, 10, 27, 15))
    assert r and decor._load()["items"][0]["cond"] == 100
    r = decor.act("알다프", {"do": "remove", "item": "약초밭", "near": "오아시스 마법도서관"}, datetime(2026, 10, 27, 16))
    assert r["text"].endswith("치워 버렸다") and decor._load()["items"] == []
    assert "마을 방어 배치" not in decor.prompt_block()


def test_not_enough_fund(monkeypatch):
    monkeypatch.setattr(decor, "START_FUND", 50000)
    assert decor.act("리그루드", {"do": "place", "item": "결계석", "near": "별빛 환영극장"}, datetime(2026, 10, 16, 15)) is None


def test_open_district_and_render(monkeypatch, tmp_path):
    from poring_food import townmap
    if not townmap._font_file():
        pytest.skip("글꼴 없음")
    monkeypatch.setattr(decor, "DECOR_DIR", str(tmp_path))
    now = datetime(2026, 10, 16, 20, 3)
    decor.act("쿠로베", {"do": "place", "item": "룬 벽화", "near": "길드 주점"}, now)
    assert decor.should_render(now)
    path = decor.render(now)
    from PIL import Image
    w = Image.open(path).size[0]
    assert decor.open_district("upper") and not decor.open_district("upper")
    assert Image.open(decor.render(now)).size[0] == w and Image.open(decor.render(now)).size[1] > 500   # 위로 넓어짐
    decor.mark_rendered(now)
    assert not decor.should_render(now)


def test_handle_decor_posts_and_pays(monkeypatch):
    from poring_food import agents, life
    posts = []
    monkeypatch.setattr(agents, "_post", lambda a, text, at=None: posts.append((a["name"], text)))
    by_name = {a["name"]: a for a in agents.load_agents()}
    money = life.get("카즈마")["money"]
    decisions = {"카즈마": {"state": "일하는 중", "decor": {"do": "place", "item": "목책", "near": "별빛 환영극장", "why": "극장 앞 길목 막으려고"}}}
    agents._handle_decor(decisions, by_name, {}, datetime(2026, 10, 16, 15, 3), "h", {})
    assert posts and posts[0][0] == "카즈마" and "별빛 환영극장 근처에 목책" in posts[0][1] and "방어 기금" in posts[0][1]
    assert life.get("카즈마")["money"] == money + decor.WAGE
    assert "목책" in decor.prompt_block()


def test_summary_for_admin_command():
    assert "아직 아무것도 놓이지 않았어요" in decor.summary()
    now = datetime(2026, 10, 16, 15, 0)
    decor.act("카즈마", {"do": "place", "item": "약초밭", "near": "오아시스 마법도서관"}, now)
    d = decor._load()
    d["items"][0]["cond"] = 10
    decor._save(d)
    text = decor.summary(now)
    assert "방어 기금" in text and "오아시스 마법도서관 근처 약초밭 - 낡음/시듦 (10)" in text
    assert "손봐야 할 것 1개" in text and "카즈마: 오아시스 마법도서관 근처 약초밭 세움" in text
    assert "오늘 배치 1/4번 (카즈마)" in text and "북쪽 성벽길" in text and len(text) <= 1900


def test_people_on_board(monkeypatch, tmp_path):
    states = {"1": {"name": "애순이", "location": "오아시스 마법도서관(계산대)", "state": "일하는 중"},
              "2": {"name": "소라", "location": "찻집 오후세시", "state": "일하는 중"},
              "3": {"name": "손님", "location": "별빛 환영극장 1층 찻집 오후세시", "state": "개인시간"},
              "4": {"name": "카즈마", "location": "골목", "state": "일하는 중"},
              "5": {"name": "잠꾸러기", "location": "찻집 오후세시", "state": "자는 중"},
              "6": {"name": "김선영", "location": "집", "state": "개인시간"}}
    people = decor.people_from_states(states, {"애순이", "소라", "카즈마", "김선영"})
    assert people == {"오아시스 마법도서관": {"agents": ["애순이"], "others": 0},
                      "찻집 오후세시": {"agents": ["소라"], "others": 1},
                      "마을 길": {"agents": ["카즈마"], "others": 0}}
    from poring_food import townmap
    if not townmap._font_file():
        pytest.skip("글꼴 없음")
    monkeypatch.setattr(decor, "DECOR_DIR", str(tmp_path))
    from PIL import Image
    plain = Image.open(decor.render(datetime(2026, 10, 16, 20))).size[0]
    assert Image.open(decor.render(datetime(2026, 10, 16, 20), people)).size[0] > plain   # 오른쪽 "지금 어디에"
