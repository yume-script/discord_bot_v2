"""RSS 알림 기사 모으기·찾기·본문 뽑기 (ai/rss_feed.py)"""
import asyncio
import time
from types import SimpleNamespace as NS

import pytest

from ai import rss_feed as rf


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "STORE_PATH", tmp_path / "rss_feed.json")
    monkeypatch.setattr(rf, "_backfilled", True)
    monkeypatch.setattr(rf, "CHANNEL_IDS", {"111"})
    monkeypatch.setattr(rf, "WEBHOOK_NAMES", {"NEW_RSS_FEED.GAS"})


def _embed(title, link, footer="rss_www_ddanzi_com_rss"):
    return NS(description=f"🧧 [{title}]({link})", footer=NS(text=footer))


def _msg(embeds, channel="111", name="NEW_RSS_FEED.GAS", at=None):
    created = NS(timestamp=lambda: at or time.time())
    return NS(channel=NS(id=int(channel)), author=NS(name=name, display_name=name), embeds=embeds, created_at=created)


def test_capture_and_dedupe():
    m = _msg([_embed("[속보] 첫 기사 (사진)", "https://a.example/1?x=1&amp;y=2"), _embed("둘째", "https://a.example/2")])
    assert rf.capture(m) == 2
    assert rf.capture(m) == 0     # 같은 링크는 한 번
    items = rf._load()
    assert {x["title"] for x in items} == {"[속보] 첫 기사 (사진)", "둘째"}
    assert items[0]["source"] == "딴지일보"
    assert any(x["link"] == "https://a.example/1?x=1&y=2" for x in items)


def test_capture_ignores_other_channels_and_names():
    assert rf.capture(_msg([_embed("a", "https://a.example/1")], channel="222")) == 0
    assert rf.capture(_msg([_embed("a", "https://a.example/1")], name="다른알림.GAS")) == 0
    assert rf._load() == []


def test_search_find_and_tool():
    now = time.time()
    rf.add([{"title": "반도체 수출 늘어", "link": "https://n/1", "source": "c_news", "at": now - 3600},
            {"title": "오래된 반도체 이야기", "link": "https://n/2", "source": "c_news", "at": now - 100 * 3600},
            {"title": "날씨 맑음", "link": "https://n/3", "source": "c_news", "at": now - 60}])
    assert [x["title"] for x in rf.search("반도체", hours=48, now=now)] == ["반도체 수출 늘어"]
    assert len(rf.search("반도체", hours=168, now=now)) == 2
    assert rf.find("1")["title"] == "날씨 맑음"                 # 최근 순 번호
    assert rf.find("https://n/2")["title"] == "오래된 반도체 이야기"
    assert rf.find("수출")["link"] == "https://n/1"
    out = asyncio.run(rf.search_feed_articles.ainvoke({"keyword": "반도체"}))
    assert "2. [c_news] 반도체 수출 늘어" in out and "https://n/1" in out


def test_read_only_stored_links(monkeypatch):
    called = []

    async def fake(link):
        called.append(link)
        return "제목", "본문 내용"
    monkeypatch.setattr(rf, "fetch_article", fake)
    out = asyncio.run(rf.read_feed_article.ainvoke({"article": "http://192.168.0.1/admin"}))
    assert "찾지 못했어요" in out and not called
    rf.add([{"title": "기사", "link": "https://n/9", "source": "s", "at": time.time()}])
    out = asyncio.run(rf.read_feed_article.ainvoke({"article": "1"}))
    assert called == ["https://n/9"] and "본문 내용" in out and "https://n/9" in out


def test_extract_text_prefers_article():
    para = "기사 본문 문장이 충분히 길게 이어집니다. " * 3
    html = f"""<html><head><meta property="og:title" content="진짜 제목"><title>사이트</title></head>
    <body><nav><p>{'메뉴 메뉴 메뉴 메뉴 메뉴 메뉴 메뉴'}</p></nav>
    <article><p>{para}</p><p>{para}</p><p>짧음</p></article><footer><p>저작권 문구가 길게 들어갑니다 아주 길게</p></footer>
    <script>var x = 1;</script></body></html>""".encode("utf-8")
    title, body = rf.extract_text(html)
    assert title == "진짜 제목"
    assert "기사 본문 문장" in body and "메뉴" not in body and "저작권" not in body and "var x" not in body


def test_extract_text_falls_back_to_description():
    title, body = rf.extract_text('<html><head><meta property="og:description" content="요약 설명"></head><body></body></html>'.encode())
    assert body == "요약 설명"
