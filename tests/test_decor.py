"""골목 꾸미기 판: 꾸밀 수 있는 인물만, 가게 앞 빈칸에 놓기/고치기/치우기, 기금, 매일 닳기와 비, 판 그림."""
from datetime import datetime

import pytest

from poring_food import decor


def test_roles_and_parse():
    assert decor.prompt_rule("홍두식") and "골목 기금" in decor.prompt_rule("홍두식")
    assert decor.prompt_rule("애순이") == ""                                   # 꾸미는 사람이 아니면 규칙 없음
    assert decor.parse({"do": "place", "item": "화단", "near": "책방 오아시스 앞"}, "홍두식")["near"] == "책방 오아시스"
    assert decor.parse({"do": "place", "item": "벽화", "near": "LP바"}, "홍두식") is None     # 벽화는 박충섭만
    assert decor.parse({"do": "place", "item": "벽화", "near": "LP바"}, "박충섭")["item"] == "벽화"
    assert decor.parse({"do": "place", "item": "화단"}, "부상길") is None                  # 건물주는 치우기만
    assert decor.parse({"do": "place", "item": "분수대"}, "여화정") is None


def test_place_repair_remove_and_fund():
    now = datetime(2026, 10, 16, 15, 0)
    r = decor.act("홍두식", {"do": "place", "item": "화단", "near": "책방 오아시스"}, now)
    assert r["text"] == "책방 오아시스 앞에 화단을(를) 놓았다" and r["wage"] == decor.WAGE
    assert r["fund"] == decor.START_FUND - 30000 - decor.WAGE
    item = decor._load()["items"][0]
    assert not decor._blocked(item["x"], item["y"]) and decor._near_name(item["x"], item["y"]) == "책방 오아시스"
    assert decor.act("홍두식", {"do": "place", "item": "벤치", "near": "별빛극장"}, now) is None     # 하루 한 번
    # 여러 날 닳으면 낡음 -> 사건, 비 오면 화단은 살아난다
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
    # 통장이 고치고, 건물주가 치운다
    r = decor.act("여화정", {"do": "repair", "item": "화단", "near": ""}, datetime(2026, 10, 27, 15))
    assert r and decor._load()["items"][0]["cond"] == 100
    r = decor.act("부상길", {"do": "remove", "item": "화단", "near": "책방 오아시스"}, datetime(2026, 10, 27, 16))
    assert r["text"].endswith("치워 버렸다") and decor._load()["items"] == []
    assert "골목에 꾸민 것" not in decor.prompt_block()


def test_not_enough_fund(monkeypatch):
    monkeypatch.setattr(decor, "START_FUND", 50000)
    assert decor.act("여화정", {"do": "place", "item": "포토존", "near": "별빛극장"}, datetime(2026, 10, 16, 15)) is None


def test_open_district_and_render(monkeypatch, tmp_path):
    from poring_food import townmap
    if not townmap._font_file():
        pytest.skip("글꼴 없음")
    monkeypatch.setattr(decor, "DECOR_DIR", str(tmp_path))
    now = datetime(2026, 10, 16, 20, 3)
    decor.act("박충섭", {"do": "place", "item": "벽화", "near": "LP바"}, now)
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
    money = life.get("홍두식")["money"]
    decisions = {"홍두식": {"state": "일하는 중", "decor": {"do": "place", "item": "벤치", "near": "별빛극장", "why": "극장 손님 쉬라고"}}}
    agents._handle_decor(decisions, by_name, {}, datetime(2026, 10, 16, 15, 3), "h", {})
    assert posts and posts[0][0] == "홍두식" and "별빛극장 앞에 벤치" in posts[0][1] and "골목 기금" in posts[0][1]
    assert life.get("홍두식")["money"] == money + decor.WAGE
    assert "벤치" in decor.prompt_block()


def test_summary_for_admin_command():
    assert "아직 아무것도 놓이지 않았어요" in decor.summary()
    now = datetime(2026, 10, 16, 15, 0)
    decor.act("홍두식", {"do": "place", "item": "화단", "near": "책방 오아시스"}, now)
    d = decor._load()
    d["items"][0]["cond"] = 10
    decor._save(d)
    text = decor.summary(now)
    assert "골목 기금" in text and "책방 오아시스 앞 화단 - 낡음/시듦 (10)" in text
    assert "손봐야 할 것 1개" in text and "홍두식: 책방 오아시스 앞 화단 놓음" in text
    assert "오늘 꾸미기 1/3번 (홍두식)" in text and "윗골목" in text and len(text) <= 1900
