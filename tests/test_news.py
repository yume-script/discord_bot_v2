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
    erinn = {"name": "리정혁", "company": "에린 로지스틱스 (Erinn Logistics)"}
    town = {"name": "홍두식", "company": "광주 동네 상가"}
    pe, pt = news.for_agent(erinn, t), news.for_agent(town, t)
    assert {i["category"] for i in pe} <= {"경제", "IT", "물류업계", "과학", "검색어"}
    assert any(i["category"] == "검색어" for i in pe)
    assert any(i["title"] == "광주 비엔날레 개막" for i in pt)
    assert news.for_agent(erinn, t) == pe                                  # 같은 갱신 안에선 같은 소식
    block = news.prompt_block(erinn, t)
    assert "오늘 본 뉴스" in block and "지어내지" in block
    assert "실제 뉴스" in news.world_block(t)
    assert "한국시리즈" in news.topic_line(t)
    # 며칠 지나면 빠진다
    assert news.current(t + timedelta(days=news.FRESH_DAYS + 1)) == []


def test_signals_topic_uses_real_news():
    news.refresh(datetime.now(), _fake)
    assert "요즘 검색어" in signals.collect(include_factory=False).get("topic", "")


def test_fetch_failure_keeps_previous():
    t = datetime(2026, 10, 13, 7, 3)
    news.refresh(t, _fake)
    assert not news.refresh(t.replace(hour=12), lambda: [])
    assert news.current(t.replace(hour=12))                                 # 직전 것을 계속 쓴다
