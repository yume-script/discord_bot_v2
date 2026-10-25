"""인물들이 봇 명령어를 쓴다: /주식 /환율 (실제 시세 가상 투자), /운세, /그림, /개미소리."""
from datetime import datetime, timedelta

from poring_food import agents, hobbies, life, outbox

T = datetime(2026, 10, 13, 10, 3)   # 화요일


def _quote(price):
    return lambda ticker: {"price": price if ticker.endswith(".KS") else 100.0, "prev": price * 0.98 if ticker.endswith(".KS") else 100.0,
                           "currency": "KRW" if ticker.endswith(".KS") else "USD"}


def _refresh(now=T, price=50_000.0):
    hobbies.refresh(now, quote=_quote(price), fx=lambda: {"USD": 1400.0, "JPY": 9.2, "EUR": 1500.0},
                    fortune=lambda z: f"{z}띠: 귀인을 만나는 날", agents=agents.load_agents())


def _by(name):
    return next(a for a in agents.load_agents() if a["name"] == name)


def test_market_prompt_and_trade_roundtrip():
    _refresh()
    kwon = _by("덴켄")
    block = hobbies.prompt_block(kwon, T)
    assert "실제 주식 시세" in block and "삼성전자 50,000원(+2.0%)" in block and "USD 1,400원" in block
    assert "귀인을" in block and "띠" in block
    rule, spec = hobbies.rules_and_spec(kwon, T)
    assert "주식" in rule and '"stock"' in spec
    money = int(life.get("덴켄")["money"])
    r = hobbies.trade("덴켄", {"action": "매수", "name": "삼성전자", "amount": 10 ** 9}, T)
    budget = min(hobbies.MAX_BUY, int(money * hobbies.MAX_BUY_RATIO))
    shares = int(budget // 50_000)
    assert r["command"] == f"/주식 삼성전자 매수 {shares}주"
    assert int(life.get("덴켄")["money"]) == money - shares * 50_000
    assert hobbies.holdings("덴켄")["삼성전자"]["shares"] == shares
    assert hobbies.trade("덴켄", {"action": "매도", "name": "삼성전자", "amount": 0}, T) is None   # 하루 한 번
    assert "내 상단 지분(주식)" in hobbies.prompt_block(kwon, T)
    # 다음 날 값이 오른 뒤 전량 매도 - 손익이 이야기 속 돈에 반영
    nxt = T + timedelta(days=1)
    _refresh(nxt, 60_000.0)
    r = hobbies.trade("덴켄", {"action": "매도", "name": "삼성전자", "amount": 0}, nxt)
    assert r["change"] == shares * 10_000 and int(life.get("덴켄")["money"]) == money + shares * 10_000
    assert not hobbies.holdings("덴켄")


def test_daily_mood_after_close():
    _refresh()
    hobbies.trade("덴켄", {"action": "매수", "name": "삼성전자", "amount": 2_000_000}, T)
    assert hobbies.daily_moods(T) == []                                   # 장 마감 전
    moods = dict(hobbies.daily_moods(T.replace(hour=16)))
    assert moods["덴켄"] > 0                                             # 오늘 +2%
    assert hobbies.daily_moods(T.replace(hour=17)) == []                  # 하루 한 번


def test_parse_fields():
    out = {"stock": {"action": "매수", "name": "카카오", "amount": "300,000원"},
           "drawing": {"title": "골목의 저녁", "prompt": "a quiet alley at dusk, watercolor"}, "ant_voice": "부장님 제발"}
    p = hobbies.parse(out, _by("덴켄"))
    assert p["stock"]["amount"] == 300000 and p["drawing"]["title"] == "골목의 저녁" and p["ant_voice"] == "부장님 제발"
    assert hobbies.parse({"stock": {"action": "공매도", "name": "카카오"}}, _by("덴켄")) == {}


def test_handle_hobbies_posts_commands(monkeypatch):
    monkeypatch.setattr(outbox, "paced", lambda: True)
    _refresh()
    by_name = {a["name"]: a for a in agents.load_agents()}
    decisions = {
        "덴켄": {"state": "일하는 중", "stock": {"action": "매수", "name": "카카오", "amount": 500_000},
                 "ant_voice": "주말 출근 실화냐"},
        "쿠로베": {"state": "개인시간", "drawing": {"title": "골목의 저녁", "prompt": "a quiet alley at dusk"}},
    }
    data, trig = {}, {}
    agents._handle_hobbies(decisions, by_name, data, T, "2026-10-13 10", trig)
    items = outbox.take_due(T + timedelta(hours=1), limit=50)
    kinds = [(it["kind"], it.get("name")) for it in items]
    assert ("draw", "쿠로베") in kinds and ("ant", "덴켄") in kinds
    texts = [it.get("text", "") for it in items]
    assert any(t.endswith("/그림 a quiet alley at dusk") for t in texts)
    assert any("/주식 카카오 매수" in t for t in texts) and any(it.get("name") == agents.STOCK_APP for it in items)
    assert any("/개미소리 주말 출근 실화냐" in t for t in texts)
    assert sum(1 for it in items if it.get("name") == agents.FORTUNE_NAME) >= 1   # 아침 /운세 몇 명
    # 하루 한 장 - 다시 해도 그림은 안 생긴다
    agents._handle_hobbies({"쿠로베": {"state": "개인시간", "drawing": {"title": "x", "prompt": "y"}}},
                           by_name, data, T, "2026-10-13 11", trig)
    assert not [it for it in outbox.take_due(T + timedelta(hours=2), limit=50) if it["kind"] == "draw"]
