"""
RSS 알림 기사 - 구글 앱스 스크립트(NEW_RSS_FEED.GAS)가 디스코드 알림 스레드에 올리는 기사를 모아 두고,
대화에서 "오늘 올라온 뉴스 뭐 있어?" / "그 기사 요약해 줘"에 쓰는 도구 둘.

- 모으기: cogs/chat.py가 그 웹훅 메시지를 (대답하지 않고) capture()에 넘긴다. 임베드 설명 "🧧 [제목](링크)"과
  꼬리말(피드 이름)에서 제목·링크·출처를 뽑아 storage/rss_feed.json에 최근 MAX_ITEMS건, KEEP_DAYS일 보관한다.
  봇을 켠 뒤 처음 찾을 때 스레드의 최근 메시지를 한 번 훑어 그 전에 올라온 기사도 채운다(backfill).
- 찾기: search_feed_articles(키워드, 최근 몇 시간) -> 번호·제목·출처·시각·링크.
- 요약: read_feed_article(번호/링크/제목 일부) -> 그 기사 페이지 본문(앞부분). 요약은 LLM이 이 글로 한다.
  모아 둔 기사의 링크만 연다 (아무 주소나 열면 봇이 있는 내부망 주소를 대신 열어 주는 통로가 되므로).

설정 (.env): RSS_FEED_CHANNEL_IDS(알림 스레드/채널, 쉼표), RSS_FEED_WEBHOOK_NAMES(웹훅 이름, 쉼표)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from config import settings

log = logging.getLogger("rss_feed")

CHANNEL_IDS = {s.strip() for s in os.environ.get("RSS_FEED_CHANNEL_IDS", "1350227028154126376").split(",") if s.strip()}
WEBHOOK_NAMES = {s.strip() for s in os.environ.get("RSS_FEED_WEBHOOK_NAMES", "NEW_RSS_FEED.GAS").split(",") if s.strip()}
STORE_PATH = settings.BASE_DIR / "storage" / "rss_feed.json"
MAX_ITEMS = 300
KEEP_DAYS = 7
BACKFILL_LIMIT = 200
ARTICLE_MAX_CHARS = 5000
FETCH_TIMEOUT = 15
_KST = timezone(timedelta(hours=9))
_LINK_RE = re.compile(r"\[(?P<title>.+)\]\((?P<link>https?://[^\s)]+)\)\s*$", re.S)
_SOURCES = {"ddanzi": "딴지일보", "c_news": "c_news"}
_backfilled = False


# ------------------------------------------------------------------ 저장
def _load() -> list[dict]:
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data.get("items", []) if isinstance(x, dict) and x.get("link")]
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    cut = time.time() - KEEP_DAYS * 86400
    items = sorted((x for x in items if x.get("at", 0) >= cut), key=lambda x: x.get("at", 0), reverse=True)[:MAX_ITEMS]
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False)
    os.replace(tmp, STORE_PATH)


def source_name(footer: str) -> str:
    """GAS 꼬리말 'rss_www_ddanzi_com_rss' -> '딴지일보' (모르는 건 'rss_'만 뗀다)"""
    raw = (footer or "").strip()
    for key, name in _SOURCES.items():
        if key in raw:
            return name
    return raw[4:] if raw.startswith("rss_") else raw


def parse_embeds(embeds, at: float) -> list[dict]:
    """디스코드 임베드들(description '🧧 [제목](링크)', footer.text 피드 이름) -> 기사들"""
    out = []
    for e in embeds or []:
        m = _LINK_RE.search((getattr(e, "description", None) or "").strip())
        if not m:
            continue
        footer = getattr(getattr(e, "footer", None), "text", None) or ""
        out.append({"title": m.group("title").strip(), "link": m.group("link").replace("&amp;", "&"),
                    "source": source_name(footer), "at": at})
    return out


def is_feed_message(message) -> bool:
    """알림 스레드에 RSS 웹훅이 올린 메시지인가"""
    if CHANNEL_IDS and str(getattr(message.channel, "id", "")) not in CHANNEL_IDS:
        return False
    names = {(message.author.name or "").strip(), (getattr(message.author, "display_name", "") or "").strip()}
    return bool(names & WEBHOOK_NAMES)


def add(articles: list[dict]) -> int:
    """새 기사만 더한다 (같은 링크는 한 번). 더한 수"""
    if not articles:
        return 0
    items = _load()
    have = {x["link"] for x in items}
    new = [a for a in articles if a["link"] not in have]
    if new:
        _save(new + items)
    return len(new)


def capture(message) -> int:
    """[chat.on_message] RSS 웹훅 메시지면 기사를 모아 둔다. 모은 수 (아니면 0)"""
    if not is_feed_message(message):
        return 0
    try:
        return add(parse_embeds(message.embeds, message.created_at.timestamp()))
    except Exception:  # noqa: BLE001 - 대화 처리를 막지 않는다
        log.exception("RSS 기사 저장 실패")
        return 0


async def backfill() -> int:
    """봇을 켠 뒤 한 번: 알림 스레드의 최근 메시지에서 기사를 채운다 (그 전에 올라온 것도 찾을 수 있게)"""
    global _backfilled
    if _backfilled:
        return 0
    from ai import discord_reader
    bot = discord_reader._bot
    if bot is None:
        return 0
    _backfilled = True
    got = []
    for cid in CHANNEL_IDS:
        try:
            ch = bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
            async for msg in ch.history(limit=BACKFILL_LIMIT):
                if is_feed_message(msg):
                    got += parse_embeds(msg.embeds, msg.created_at.timestamp())
        except Exception:  # noqa: BLE001
            log.warning("RSS 알림 채널 %s을 못 읽음", cid, exc_info=True)
    return add(got)


# ------------------------------------------------------------------ 찾기
def search(keyword: str = "", hours: int = 48, limit: int = 10, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    hours = max(1, min(int(hours or 48), KEEP_DAYS * 24))
    words = [w.lower() for w in (keyword or "").split() if w]
    out = []
    for x in _load():
        if x.get("at", 0) < now - hours * 3600:
            continue
        text = f"{x.get('title', '')} {x.get('source', '')}".lower()
        if all(w in text for w in words):
            out.append(x)
    return out[:max(1, min(int(limit or 10), 30))]


def _when(at: float) -> str:
    return datetime.fromtimestamp(at, _KST).strftime("%m/%d %H:%M")


def find(article: str) -> dict | None:
    """번호(최근 순 1부터)·링크·제목 일부로 모아 둔 기사 하나"""
    items, q = _load(), (article or "").strip()
    if not q or not items:
        return None
    if q.isdigit():
        n = int(q)
        return items[n - 1] if 1 <= n <= len(items) else None
    for x in items:
        if x["link"] == q:
            return x
    words = q.lower().split()
    return next((x for x in items if all(w in x.get("title", "").lower() for w in words)), None)


# ------------------------------------------------------------------ 본문
def extract_text(html: bytes | str) -> tuple[str, str]:
    """기사 페이지 -> (제목, 본문). <article>/본문 영역의 문단을 우선, 없으면 og:description, 그것도 없으면 페이지 글"""
    soup = BeautifulSoup(html, "lxml")
    og = soup.find("meta", property="og:title")
    title = (og.get("content") if og else "") or (soup.title.get_text(strip=True) if soup.title else "")
    for t in soup(["script", "style", "noscript", "nav", "header", "footer", "aside", "form", "iframe"]):
        t.decompose()
    root = (soup.find("article") or soup.find(attrs={"itemprop": "articleBody"})
            or soup.find(class_=re.compile(r"(article|news|post|read|view)[_-]?(body|content|text)", re.I))
            or soup.find(id=re.compile(r"(article|news|post|read|view)[_-]?(body|content|text)", re.I)))
    paras = [p.get_text(" ", strip=True) for p in (root or soup).find_all(["p", "li"]) if p.get_text(strip=True)]
    body = "\n".join(p for p in paras if len(p) >= 20)
    if len(body) < 200 and root is not None:
        body = root.get_text("\n", strip=True)
    if len(body) < 100:
        desc = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
        body = (desc.get("content") if desc else "") or (soup.body.get_text("\n", strip=True) if soup.body else "")
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title.strip(), body[:ARTICLE_MAX_CHARS]


async def fetch_article(link: str) -> tuple[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; discord-bot article reader)", "Accept-Language": "ko,en;q=0.8"}
    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True, headers=headers) as client:
        r = await client.get(link)
        r.raise_for_status()
        return extract_text(r.content)


# ------------------------------------------------------------------ LLM 도구
@tool
async def search_feed_articles(keyword: str = "", hours: int = 48, limit: int = 10) -> str:
    """디스코드 알림방에 RSS로 올라온 최근 뉴스·커뮤니티 기사 목록을 찾는다. "오늘 올라온 뉴스 뭐 있어?",
    "아까 ○○ 기사 뭐였지?"처럼 알림으로 올라온 기사를 물을 때 쓴다. keyword를 주면 제목·출처에 그 말이 든
    기사만(띄어쓰기로 여러 단어는 모두 포함), hours는 최근 몇 시간(기본 48, 최대 168), limit 기본 10.
    결과의 번호를 read_feed_article에 넘기면 본문을 읽어 요약할 수 있다."""
    await backfill()
    items = search(keyword, hours, limit)
    if not items:
        return "조건에 맞는 알림 기사가 없어요." + (" (아직 모아 둔 기사가 없어요)" if not _load() else "")
    order = {x["link"]: i for i, x in enumerate(_load(), 1)}
    return "\n".join(f"{order.get(x['link'], '?')}. [{x.get('source', '')}] {x['title']} ({_when(x['at'])}) {x['link']}"
                     for x in items)


@tool
async def read_feed_article(article: str) -> str:
    """알림방에 올라온 기사의 본문을 읽어 온다 (요약해 달라고 할 때). article은 search_feed_articles 결과의
    번호, 그 기사 링크, 또는 제목 일부. 모아 둔 알림 기사만 열 수 있다. 돌려준 본문으로 핵심을 3~5줄로
    요약하고 출처 링크를 같이 알려 줘 - 본문에 없는 내용은 지어내지 마."""
    await backfill()
    x = find(article)
    if x is None:
        return "그 기사를 알림 기사 목록에서 찾지 못했어요. search_feed_articles로 먼저 찾아 번호를 넘겨 주세요."
    try:
        title, body = await fetch_article(x["link"])
    except Exception as e:  # noqa: BLE001
        return f"기사 페이지를 열지 못했어요 ({type(e).__name__}). 제목: {x['title']} / 링크: {x['link']}"
    if not body:
        return f"본문을 찾지 못했어요 (로그인·사진 위주 페이지일 수 있어요). 제목: {x['title']} / 링크: {x['link']}"
    return f"제목: {title or x['title']}\n출처: {x.get('source', '')} ({_when(x['at'])})\n링크: {x['link']}\n\n본문:\n{body}"


RSS_FEED_TOOLS = [search_feed_articles, read_feed_article]
