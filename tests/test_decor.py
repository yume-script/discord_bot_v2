"""사옥 설비 배치 판: 놓을 수 있는 인물만, 장소 근처 빈칸에 세우기/고치기/치우기, 기금, 매일 닳기와 비, 판 그림."""
from datetime import datetime

import pytest

from poring_food import decor


def test_roles_and_parse():
    assert decor.prompt_rule("민재현") and "설비 기금" in decor.prompt_rule("민재현")
    assert decor.prompt_rule("애순이") == ""                                   # 놓는 사람이 아니면 규칙 없음
    assert decor.parse({"do": "place", "item": "휴게실 화분", "near": "ZEEPS 편집국 앞"}, "민재현")["near"] == "ZEEPS 편집국"
    assert decor.parse({"do": "place", "item": "응원 벽화", "near": "솥뚜껑 주점"}, "민재현") is None     # 응원 벽화는 구본우만
    assert decor.parse({"do": "place", "item": "응원 벽화", "near": "솥뚜껑 주점"}, "구본우")["item"] == "응원 벽화"
    assert decor.parse({"do": "place", "item": "휴게실 화분"}, "안대풍") is None                  # 건물주는 치우기만
    assert decor.parse({"do": "place", "item": "분수대"}, "구영철") is None


def test_place_repair_remove_and_fund():
    now = datetime(2026, 10, 16, 15, 0)
    r = decor.act("민재현", {"do": "place", "item": "휴게실 화분", "near": "ZEEPS 편집국"}, now)
    assert r["text"] == "ZEEPS 편집국 근처에 휴게실 화분을 세웠다" and r["wage"] == decor.WAGE
    assert r["fund"] == decor.START_FUND - 30000 - decor.WAGE
    item = decor._load()["items"][0]
    assert not decor._blocked(item["x"], item["y"]) and decor._near_name(item["x"], item["y"]) == "ZEEPS 편집국"
    assert decor.act("민재현", {"do": "place", "item": "방화벽", "near": "ZEEPS 영상콘텐츠팀"}, now) is None     # 하루 한 번
    # 여러 날 닳으면 낡음 -> 사건, 비 오면 휴게실 화분은 살아난다
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
    # 번영회장이 고치고, 건물주가 치운다
    r = decor.act("구영철", {"do": "repair", "item": "휴게실 화분", "near": ""}, datetime(2026, 10, 27, 15))
    assert r and decor._load()["items"][0]["cond"] == 100
    r = decor.act("안대풍", {"do": "remove", "item": "휴게실 화분", "near": "ZEEPS 편집국"}, datetime(2026, 10, 27, 16))
    assert r["text"].endswith("치워 버렸다") and decor._load()["items"] == []
    assert "사옥 설비 배치" not in decor.prompt_block()


def test_not_enough_fund(monkeypatch):
    monkeypatch.setattr(decor, "START_FUND", 50000)
    assert decor.act("구영철", {"do": "place", "item": "메인 서버", "near": "ZEEPS 영상콘텐츠팀"}, datetime(2026, 10, 16, 15)) is None


def test_open_district_and_render(monkeypatch, tmp_path):
    from poring_food import townmap
    if not townmap._font_file():
        pytest.skip("글꼴 없음")
    monkeypatch.setattr(decor, "DECOR_DIR", str(tmp_path))
    now = datetime(2026, 10, 16, 20, 3)
    decor.act("구본우", {"do": "place", "item": "응원 벽화", "near": "솥뚜껑 주점"}, now)
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
    money = life.get("민재현")["money"]
    decisions = {"민재현": {"state": "일하는 중", "decor": {"do": "place", "item": "방화벽", "near": "ZEEPS 영상콘텐츠팀", "why": "영상팀 앞 길목 막으려고"}}}
    agents._handle_decor(decisions, by_name, {}, datetime(2026, 10, 16, 15, 3), "h", {})
    assert posts and posts[0][0] == "민재현" and "ZEEPS 영상콘텐츠팀 근처에 방화벽" in posts[0][1] and "설비 기금" in posts[0][1]
    assert life.get("민재현")["money"] == money + decor.WAGE
    assert "방화벽" in decor.prompt_block()


def test_summary_for_admin_command():
    assert "아직 아무것도 놓이지 않았어요" in decor.summary()
    now = datetime(2026, 10, 16, 15, 0)
    decor.act("민재현", {"do": "place", "item": "휴게실 화분", "near": "ZEEPS 편집국"}, now)
    d = decor._load()
    d["items"][0]["cond"] = 10
    decor._save(d)
    text = decor.summary(now)
    assert "설비 기금" in text and "ZEEPS 편집국 근처 휴게실 화분 - 낡음/시듦 (10)" in text
    assert "손봐야 할 것 1개" in text and "민재현: ZEEPS 편집국 근처 휴게실 화분 세움" in text
    assert "오늘 배치 1/4번 (민재현)" in text and "북쪽 골목" in text and len(text) <= 1900


def test_people_on_board(monkeypatch, tmp_path):
    states = {"1": {"name": "애순이", "location": "ZEEPS 편집국(계산대)", "state": "일하는 중"},
              "2": {"name": "소라", "location": "카페 오후세시", "state": "일하는 중"},
              "3": {"name": "손님", "location": "ZEEPS 영상콘텐츠팀 1층 카페 오후세시", "state": "개인시간"},
              "4": {"name": "민재현", "location": "골목", "state": "일하는 중"},
              "5": {"name": "잠꾸러기", "location": "카페 오후세시", "state": "자는 중"},
              "6": {"name": "김선영", "location": "집", "state": "개인시간"}}
    people = decor.people_from_states(states, {"애순이", "소라", "민재현", "김선영"})
    assert people == {"ZEEPS 편집국": {"agents": ["애순이"], "others": 0},
                      "카페 오후세시": {"agents": ["소라"], "others": 1},
                      "거리 길": {"agents": ["민재현"], "others": 0}}
    from poring_food import townmap
    if not townmap._font_file():
        pytest.skip("글꼴 없음")
    monkeypatch.setattr(decor, "DECOR_DIR", str(tmp_path))
    from PIL import Image
    plain = Image.open(decor.render(datetime(2026, 10, 16, 20))).size[0]
    assert Image.open(decor.render(datetime(2026, 10, 16, 20), people)).size[0] > plain   # 오른쪽 "지금 어디에"
