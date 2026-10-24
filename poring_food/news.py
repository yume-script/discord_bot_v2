"""
떠돌이 상인 소문 - 이야기가 마을 안에서만 맴돌지 않게, 실제 한국 검색어와 뉴스 제목을 가져와
'떠돌이 상인이 전한 왕국 소문'으로 인물들의 얘깃거리로 쓴다 (판타지 세계에 맞게 바꿔 말하라고 프롬프트에 적는다).

출처 (키 없음, RSS):
- Google 트렌드(한국): 지금 많이 검색되는 검색어 + 관련 기사 제목  (TRENDS_URL)
- Google 뉴스(한국): 분야별 헤드라인 (경제/IT/연예/스포츠/건강/과학) + 업계 검색(식품/콘텐츠/물류/광주)

- 하루 REFRESH_HOURS(7/12/18시) 이후 첫 회차에 새로 받는다 (LLM 호출 없음, 실패하면 직전 것을 쓴다).
- 안전 필터: 정치/선거/사건사고(사망/범죄/참사)/전쟁/혐오 키워드가 든 제목은 뺀다 (PORING_NEWS_BLOCK으로 더 넣을 수 있다).
- 처음 본 지 FRESH_DAYS일 지난 제목은 빠진다 (같은 뉴스를 며칠씩 우려먹지 않게).
- 쓰는 곳:
  · 인물 판단: 그 사람 관심 분야(가게/성격으로 정함)에 맞는 뉴스 2~3개 + 검색어 하나 (prompt_block)
  · 세계 엔진: 하루 계획 때 오늘 헤드라인 (world_block), 하루 중 새 뉴스가 들어오면 남은 시간에 사건 추가(world._news_events)
  · 바깥 세상 신호의 "오늘 세상의 화제" (topic_line) - 예전엔 LLM이 지어낸 화제 하나였다
저장: storage/poring_food/news.json {"slot", "fetched", "items": [...], "seen": {제목: 처음 본 날}}
"""
from __future__ import annotations

import json
import os
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from . import worldcfg

NEWS_PATH = os.path.join(STATE_DIR, "news.json")
ENABLED = os.getenv("PORING_NEWS", "1") not in ("0", "false", "False", "")
REFRESH_HOURS = (7, 12, 18)
FRESH_DAYS = 2
TIMEOUT = 10
PER_SOURCE = 8
TRENDS_URL = "https://trends.google.com/trending/rss?geo=KR"
_GN = "https://news.google.com/rss"
_LOCALE = "hl=ko&gl=KR&ceid=KR:ko"
SECTIONS = {"경제": "BUSINESS", "IT": "TECHNOLOGY", "연예": "ENTERTAINMENT", "스포츠": "SPORTS",
            "건강": "HEALTH", "과학": "SCIENCE"}
SEARCHES = {"출판업계": "서점 출판 웹툰", "콘텐츠업계": "OTT 드라마", "영화업계": "영화 개봉 극장", "광주": "광주 소식"}
_BLOCK = ["대통령", "국회", "의원", "여당", "야당", "정당", "민주당", "국민의힘", "선거", "탄핵", "총선", "대선", "정치",
          "검찰", "기소", "구속", "재판", "살인", "사망", "숨져", "숨진", "참사", "폭행", "성폭", "마약", "자살", "극단적",
          "전쟁", "공습", "테러", "미사일", "혐오", "북한", "사고", "화재", "실종", "학대", "흉기"]
BLOCK = _BLOCK + [w.strip() for w in os.getenv("PORING_NEWS_BLOCK", "").split(",") if w.strip()]

# 관심 분야 - 소속(가게)/역할로 정한다 (agents.json "interests"가 있으면 그걸)
_INTERESTS = {
    worldcfg.BOOK: ["연예", "콘텐츠업계", "출판업계", "경제", "광주"],
    worldcfg.CINEMA: ["연예", "콘텐츠업계", "영화업계", "IT", "경제"],
    worldcfg.STREET: ["광주", "연예", "스포츠", "건강", "경제"],
}
_DEFAULT_INTERESTS = ["연예", "스포츠", "건강", "광주"]


def _load() -> dict:
    try:
        with open(NEWS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(NEWS_PATH), exist_ok=True)
    tmp = NEWS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NEWS_PATH)


def safe(title: str) -> bool:
    return bool(title) and not any(w in title for w in BLOCK)


def _clean(title: str) -> str:
    """Google 뉴스 제목 끝의 " - 언론사"와 [단독] 같은 머리표를 뗀다."""
    t = " ".join(str(title or "").split())
    t = re.sub(r"\s+-\s+[^-]{1,30}$", "", t)
    t = re.sub(r"^(\[[^\]]{1,10}\]\s*)+", "", t)
    return t.strip()[:120]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag.rsplit(":", 1)[-1]


# ===================================================================== 받아오기 (RSS)
def parse_trends(xml: str) -> list[dict]:
    """Google 트렌드 RSS -> [{"title": 검색어, "category": "검색어", "related": 관련 기사 제목}]."""
    out = []
    for item in ET.fromstring(xml).iter("item"):
        kw = (item.findtext("title") or "").strip()
        related = ""
        for el in item.iter():
            if _local(el.tag) == "news_item_title" and (el.text or "").strip():
                related = _clean(el.text)
                break
        if kw and safe(kw) and (not related or safe(related)):
            out.append({"title": kw, "category": "검색어", "related": related})
    return out


def parse_news(xml: str, category: str) -> list[dict]:
    """Google 뉴스 RSS -> [{"title", "category", "source"}]."""
    out = []
    for item in ET.fromstring(xml).iter("item"):
        title = _clean(item.findtext("title") or "")
        if title and safe(title):
            out.append({"title": title, "category": category, "source": (item.findtext("source") or "").strip()})
    return out


def _get(url: str) -> str | None:
    try:
        res = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 (poring-food news)"})
        return res.text if res.status_code == 200 else None
    except requests.RequestException:
        return None


def fetch() -> list[dict]:
    items: list[dict] = []
    xml = _get(TRENDS_URL)
    if xml:
        try:
            items += parse_trends(xml)[:PER_SOURCE + 4]
        except ET.ParseError:
            pass
    for cat, topic in SECTIONS.items():
        xml = _get(f"{_GN}/headlines/section/topic/{topic}?{_LOCALE}")
        if xml:
            try:
                items += parse_news(xml, cat)[:PER_SOURCE]
            except ET.ParseError:
                pass
    for cat, q in SEARCHES.items():
        xml = _get(f"{_GN}/search?q={quote(q)}+when:2d&{_LOCALE}")
        if xml:
            try:
                items += parse_news(xml, cat)[:PER_SOURCE // 2]
            except ET.ParseError:
                pass
    seen, uniq = set(), []
    for it in items:
        if it["title"] not in seen:
            seen.add(it["title"])
            uniq.append(it)
    return uniq


def _slot(now: datetime) -> str:
    hs = [h for h in REFRESH_HOURS if h <= now.hour]
    return f"{now:%Y-%m-%d}-{hs[-1]}" if hs else f"{(now - timedelta(days=1)):%Y-%m-%d}-{REFRESH_HOURS[-1]}"


def refresh(now: datetime | None = None, fetcher=None) -> bool:
    """[매시 회차 시작] 갱신할 때가 됐으면 새로 받는다. 반환: 새로 받았는지."""
    if not ENABLED:
        return False
    now = now or now_kst()
    data = _load()
    slot = _slot(now)
    if data.get("slot") == slot:
        return False
    items = (fetcher or fetch)()
    data["slot"] = slot
    if not items:
        _save(data)
        print("[뉴스] 새 소식을 못 받음 - 직전 것을 계속 쓴다")
        return False
    today = now.strftime("%Y-%m-%d")
    seen = data.get("seen") or {}
    for it in items:
        seen.setdefault(it["title"], today)
    cutoff = (now - timedelta(days=14)).strftime("%Y-%m-%d")
    data["seen"] = {t: d for t, d in seen.items() if d >= cutoff}
    data["items"] = items
    data["fetched"] = now.isoformat(timespec="minutes")
    _save(data)
    print(f"[뉴스] {len(items)}개 받음 (검색어 {sum(1 for i in items if i['category'] == '검색어')}개)")
    return True


def current(now: datetime | None = None) -> list[dict]:
    """지금 쓸 소식 (처음 본 지 FRESH_DAYS일 지난 건 뺀다)."""
    now = now or now_kst()
    data = _load()
    cutoff = (now - timedelta(days=FRESH_DAYS)).strftime("%Y-%m-%d")
    seen = data.get("seen") or {}
    return [i for i in data.get("items") or [] if seen.get(i["title"], "9999") >= cutoff]


# ===================================================================== 쓰는 곳
def _line(it: dict) -> str:
    if it["category"] == "검색어":
        return f"(요즘 많이 찾는 검색어) '{it['title']}'" + (f" - {it['related']}" if it.get("related") else "")
    return f"({it['category']}) {it['title']}"


def interests(agent: dict) -> list[str]:
    return agent.get("interests") or _INTERESTS.get(agent.get("company", ""), _DEFAULT_INTERESTS)


def for_agent(agent: dict, now: datetime | None = None, k: int = 3) -> list[dict]:
    """그 사람이 오늘(이번 갱신분) 봤을 법한 소식 - 관심 분야 위주 k개 + 검색어 하나. 갱신 주기 안에선 같다."""
    now = now or now_kst()
    items = current(now)
    if not items:
        return []
    rnd = random.Random(f"news|{agent.get('name')}|{_slot(now)}")
    mine = [i for i in items if i["category"] in interests(agent)]
    rnd.shuffle(mine)
    trends = [i for i in items if i["category"] == "검색어"]
    pick = mine[:k] + ([rnd.choice(trends)] if trends else [])
    return pick


def prompt_block(agent: dict, now: datetime | None = None) -> str:
    pick = for_agent(agent, now)
    if not pick:
        return ""
    return ("[떠돌이 상인이 전한 왕국 소문 - 실제 바깥 세상 소식을 이 세계의 소문으로 들은 것. 관심 가면 얘깃거리나 생각거리로 "
            "(억지로는 말고, 내용을 지어내지 마라. 현대 물건 이름은 이 세계에 맞게 바꿔 말한다)]\n"
            + "\n".join(f"- {_line(i)}" for i in pick))


def world_block(now: datetime | None = None, k: int = 10) -> str:
    items = current(now)
    if not items:
        return ""
    rnd = random.Random(f"news-world|{_slot(now or now_kst())}")
    pick = rnd.sample(items, min(k, len(items)))
    return ("[떠돌이 상인이 전한 왕국 소문(실제 뉴스/검색어) - 어울리는 게 있으면 사건 하나쯤 이것과 이어도 좋다 "
            "(이 세계의 소문으로 바꿔서, 뉴스 내용을 지어내거나 실존 인물을 등장시키지 마라)]\n"
            + "\n".join(f"- {_line(i)}" for i in pick))


def topic_line(now: datetime | None = None) -> str:
    """바깥 세상 신호의 '오늘 세상의 화제' 한 줄 (검색어 + 헤드라인 몇 개)."""
    items = current(now)
    if not items:
        return ""
    trends = [i["title"] for i in items if i["category"] == "검색어"][:3]
    heads = [i["title"] for i in items if i["category"] in ("경제", "IT", "연예", "스포츠")][:3]
    parts = ([f"요즘 상인들이 수군대는 말: {', '.join(trends)}"] if trends else []) + heads
    return " / ".join(parts)
