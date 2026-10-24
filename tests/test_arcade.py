"""봇 오락실: 인물이 봇 미니게임을 실제 게임 엔진으로 한 판 하고, 이야기 속 돈/기분/채널에 반영된다."""
import random
from datetime import datetime, timedelta

import pytest

from poring_food import agents, arcade, life, outbox


@pytest.fixture
def money_dir():
    from core import game_engine
    return game_engine.money_system.base_dir  # conftest가 테스트마다 임시 폴더로 바꿔 둔다


def test_parse_and_bet_caps(money_dir):
    assert arcade.parse({"name": "바카라", "bet": "50,000원", "choice": "짝"}) == \
        {"name": "바카라", "bet": 50000, "choice": "짝", "why": ""}
    assert arcade.parse({"name": "용호", "bet": 1000, "choice": "모름"})["choice"] == "용"
    assert arcade.parse({"name": "포커", "bet": 1000}) is None
    name = "소라"
    money = int(life.get(name)["money"])
    r = arcade.play(name, {"name": "블랙잭", "bet": 10 ** 9, "choice": ""}, datetime(2026, 10, 12, 20))
    assert r and r["bet"] == min(arcade.MAX_BET, int(money * arcade.MAX_BET_RATIO))
    assert r["money"] == money + r["change"] == int(life.get(name)["money"])  # 이야기 속 돈에 반영
    assert r["command"].startswith("/블랙잭 ")


def test_daily_limit(money_dir, monkeypatch):
    random.seed(1)
    name = "김동식"
    plays = [arcade.play(name, {"name": "주사위", "bet": 5000, "choice": ""}) for _ in range(arcade.MAX_PLAYS_PER_DAY + 2)]
    assert sum(1 for p in plays if p) == arcade.MAX_PLAYS_PER_DAY
    assert arcade.prompt_rule(name) == ""                     # 오늘 다 했으면 판단 프롬프트에서 빠진다
    assert "블랙잭" in arcade.prompt_rule("소라")


def test_handle_arcade_posts_command_result_and_manager_comment(money_dir, monkeypatch, fake_llm):
    monkeypatch.setattr(outbox, "paced", lambda: True)
    monkeypatch.setattr(agents, "_asleep", lambda a, h: False)
    calls = []

    def handler(system, user):
        if "내기 판 앞" in user:
            calls.append(user)
            return {"say": "오늘은 여기까지.", "cut_off": True, "group_post": "오늘 대박 손님 나왔어요"}
        return {}

    fake_llm(handler)
    by_name = {a["name"]: a for a in agents.load_agents()}
    now = datetime(2026, 10, 12, 20, 3)
    decisions = {"소라": {"state": "개인시간", "game": {"name": "가위바위보", "bet": 3000, "choice": "보"}},
                 "김동식": {"state": "일하는 중", "game": {"name": "슬롯머신", "bet": 3000, "choice": ""}}}
    data, triggers = {}, {}
    agents._handle_arcade(decisions, by_name, data, now, "2026-10-12 20", triggers)
    items = outbox.take_due(now + timedelta(hours=1), limit=20)
    texts = [(it["name"], it["text"]) for it in items]
    assert texts[0][0] == "소라" and texts[0][1].startswith(f"-# 🎰 {arcade.PLACE} · ") and texts[0][1].endswith("\n/보 3000")
    assert texts[1][0] == agents.ARCADE_NAME and "변동:" in texts[1][1]
    assert texts[2][0] == arcade.MANAGER and texts[2][1].endswith(f"{arcade.MANAGER} → 소라\n오늘은 여기까지.")
    assert len(texts) == 3                                    # 3천 원짜리 판은 대박이 아니라 통신방엔 안 올린다
    assert not any(n == "김동식" for n, _ in texts)           # 일하는 중이면 안 한다
    assert any(arcade.PLACE in m["text"] for m in data["소라"]["inbox"])
    assert arcade.is_cut_off("소라", now) and arcade.prompt_rule("소라", now) == ""   # 주인이 오늘은 그만 받는다
    assert arcade.play("소라", {"name": "주사위", "bet": 3000, "choice": ""}, now) is None
    st = arcade.today_stats(now)
    assert st["plays"] == 1 and st["revenue"] == -st["by"]["소라"]


def test_arcade_kpi_post_for_manager(money_dir):
    from poring_food import characters, kpi
    kpi.assign(agents.load_agents(), characters.load_roster())
    assert kpi.holder("arcade") == arcade.MANAGER
    x = kpi.reading(kpi.POST_BY_ID["arcade"])
    assert x["score"] == 80 and "내기 판 0판" in x["text"]      # 손님 없으면 한산
    arcade._record("소라", -50000, datetime.now())
    x = kpi.reading(kpi.POST_BY_ID["arcade"])
    assert x["score"] == 110 and "+50,000원" in x["text"]
