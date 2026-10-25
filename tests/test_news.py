"""바깥 세상 뉴스: Google 트렌드/뉴스 RSS 파싱, 안전 필터, 하루 3번 갱신, 사람마다 관심 분야 소식, 며칠 지나면 빠짐."""
from datetime import datetime, timedelta

from poring_food import news, signals

TRENDS = """<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>한국시리즈</title><ht:approx_traffic>50000+</ht:approx_traffic>
  <ht:news_item><ht:news_item_title>[속보] 한국시리즈 7차전 매진 - 스포츠신문</ht:news_item_title></ht:news_item></item>
<item><title>국회의원 선거</title><ht:news_item><ht:news_item_title>선거 결과</ht:news_item_title></ht:news_item></item>
<item><title>단풍 절정</title></item>
</channel></rss>"""

NEWS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>배달앱 수수료 인하 발표 - 한국경제</title><source url="x">한국경제</source></item>
<item><title>공장 화재로 2명 사망 - 연합뉴스</title></item>
<item><title>[단독] 새 스마트폰 공개 임박 - IT조선</title></item>
</channel></rss>"""


def test_parse_and_safety_filter():
    t = news.parse_trends(TRENDS)
    assert [i["title"] for i in t] == ["한국시리즈", "단풍 절정"]           # 정치 검색어는 빠진다
    assert t[0]["related"] == "한국시리즈 7차전 매진"                     # 머리표/언론사 뗌
    n = news.parse_news(NEWS, "경제")
    assert [i["title"] for i in n] == ["배달앱 수수료 인하 발표", "새 스마트폰 공개 임박"]   # 사건사고는 빠진다


def _fake():
    return news.parse_trends(TRENDS) + news.parse_news(NEWS, "경제") + [
        {"title": "택배 물량 사상 최대", "category": "물류업계", "source": ""},
        {"title": "광주 비엔날레 개막", "category": "광주", "source": ""}]


def test_refresh_slots_and_agent_picks():
    calls = []

    def fetcher():
        calls.append(1)
        return _fake()

    t = datetime(2026, 10, 13, 7, 3)
    assert news.refresh(t, fetcher) and not news.refresh(t + timedelta(hours=2), fetcher)   # 7시 몫은 한 번
    assert news.refresh(t.replace(hour=12), fetcher) and len(calls) == 2                     # 12시에 다시
    erinn = {"name": "일레이나", "company": "별빛 환영극장"}
    town = {"name": "카즈마", "company": "오아시스 마을"}
    pe, pt = news.for_agent(erinn, t), news.for_agent(town, t)
    assert {i["category"] for i in pe} <= {"연예", "콘텐츠업계", "영화업계", "IT", "경제", "검색어"}
    assert any(i["category"] == "검색어" for i in pe)
    assert any(i["title"] == "광주 비엔날레 개막" for i in pt)
    assert news.for_agent(erinn, t) == pe                                  # 같은 갱신 안에선 같은 소식
    block = news.prompt_block(erinn, t)
    assert "떠돌이 상인" in block and "지어내지" in block
    assert "실제 뉴스" in news.world_block(t)
    assert "한국시리즈" in news.topic_line(t)
    # 며칠 지나면 빠진다
    assert news.current(t + timedelta(days=news.FRESH_DAYS + 1)) == []


def test_signals_topic_uses_real_news():
    news.refresh(datetime.now(), _fake)
    assert "요즘 상인들이 수군대는 말" in signals.collect(include_factory=False).get("topic", "")


def test_fetch_failure_keeps_previous():
    t = datetime(2026, 10, 13, 7, 3)
    news.refresh(t, _fake)
    assert not news.refresh(t.replace(hour=12), lambda: [])
    assert news.current(t.replace(hour=12))                                 # 직전 것을 계속 쓴다


def test_world_adds_events_when_new_news_arrives(fake_llm, monkeypatch):
    """하루 계획을 세운 뒤 새 뉴스가 들어오면 남은 시간에 뉴스에 반응하는 사건을 더한다 (기존 계획은 그대로)."""
    from poring_food import agents, characters, world
    calls = []

    def handler(system, user):
        if "왕국 소문" in system:
            calls.append(user)
            return {"events": [{"hour": 13, "kind": "돈/경제", "title": "배달 수수료 얘기로 상인회 술렁",
                                "detail": "수수료 뉴스에 마을 가게들이 모였다.", "targets": ["마을"], "visibility": "direct"}]}
        if "세계 엔진" in system:
            return {"events": [{"hour": 15, "title": "원래 사건", "detail": "x", "targets": ["마을"], "visibility": "direct"}]}
        return {}

    fake_llm(handler)
    ags, roster = agents.load_agents(), characters.load_roster()
    t7 = datetime(2026, 10, 13, 7, 3)
    monkeypatch.setattr(world, "now_kst", lambda: t7)
    news.refresh(t7, lambda: news.parse_trends(TRENDS))
    world.tick(ags, roster)                                      # 하루 계획 (이때 본 뉴스는 '본 것')
    assert not calls
    t13 = t7.replace(hour=13)
    monkeypatch.setattr(world, "now_kst", lambda: t13)
    news.refresh(t13, _fake)                                     # 12시 몫 새 뉴스
    out = world.tick(ags, roster)
    assert len(calls) == 1 and "배달앱 수수료" in calls[0] and "원래 사건" in calls[0]
    plan = world._load()["plan"]
    assert [e["title"] for e in plan] == ["배달 수수료 얘기로 상인회 술렁", "원래 사건"]
    assert plan[0]["from_news"] and plan[0]["released"]          # 13시 사건이라 바로 터진다
    assert any("수수료 뉴스" in text for _, text, _ in out)
    world.tick(ags, roster)                                      # 같은 뉴스로는 다시 안 부른다
    assert len(calls) == 1
