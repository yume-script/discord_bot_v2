"""
봇 명령어를 인물들이 직접 쓴다 - /주식, /환율, /운세, /그림, /개미소리.

1) 주식/환율 (가상 투자, 실제 시세): 매시 회차 시작에 시세(Yahoo 금융)와 환율(Frankfurter)을 받아 둔다(키 없음, 한 시간 캐시).
   - 경제에 관심 있는 인물(news.interests에 "경제")이나 주식을 가진 인물은 판단 때 시세/환율과 내 보유 종목을 보고,
     하고 싶으면 "stock"(매수/매도)을 고른다 (LLM 호출 추가 없음). 돈은 이야기 속 돈(life money), 한 사람 하루 1번.
   - 매수: 가진 돈의 MAX_BUY_RATIO, MAX_BUY 이하로 정수 주. 매도: 그 종목 전량.
   - 장 마감 뒤(MOOD_HOUR시 이후 첫 회차) 오늘 평가손익으로 기분이 바뀌고, 크게 움직이면 기억/알림.
2) 운세: 하루 한 번(아침) 인물들 띠의 운세를 네이버(봇의 /운세와 같은 방식)에서 받아 판단에 넣는다.
   띠는 agents.json "zodiac" 또는 이름으로 정한다. 아침에 몇 명(FORTUNE_POSTS_PER_DAY)은 /운세를 친 것처럼 채널에 올린다.
3) 그림: 화가 인물(ARTISTS, 기본 쿠로베/마르실)이 그리고 싶을 때 "drawing"(제목/영어 프롬프트)을 고르면 봇의 이미지 생성(/그림)으로
   실제 그림을 올린다. 하루 DRAW_PER_DAY장, 한 사람 하루 1장. 생성은 오래 걸려서 봇이 예약함에서 꺼낼 때 한다(outbox "draw").
4) 개미소리: 가게 직원(도서관/극장)이 속이 터질 때 "ant_voice"(짧은 외침)를 고르면 봇의 /개미소리 짤을 통신방에 올린다
   (outbox "ant"). 하루 ANT_PER_DAY장, 한 사람 하루 1장.
저장: storage/poring_food/market.json(시세/보유), fortune.json(오늘 운세), hobbies.json(오늘 횟수)
"""
from __future__ import annotations

import json
import os
import random
import re
from datetime import datetime

import requests

from . import life, news, worldcfg
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

MARKET_PATH = os.path.join(STATE_DIR, "market.json")
FORTUNE_PATH = os.path.join(STATE_DIR, "fortune.json")
HOBBY_PATH = os.path.join(STATE_DIR, "hobbies.json")
ENABLED = os.getenv("PORING_HOBBIES", "1") not in ("0", "false", "False", "")
TIMEOUT = 10

# ----------------------------------------------------------------- 주식/환율
STOCKS = {"삼성전자": "005930.KS", "SK하이닉스": "000660.KS", "NAVER": "035420.KS", "카카오": "035720.KS",
          "현대차": "005380.KS", "셀트리온": "068270.KS", "하이브": "352820.KS", "크래프톤": "259960.KS",
          "엔씨소프트": "036570.KS", "CJ제일제당": "097950.KS", "애플": "AAPL", "엔비디아": "NVDA", "테슬라": "TSLA"}
FX = ("USD", "JPY", "EUR")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
FRANKFURTER = "https://api.frankfurter.app/latest"
MAX_BUY = int(os.getenv("PORING_STOCK_MAX_BUY", "2000000"))
MAX_BUY_RATIO = 0.3
MIN_BUY = 10_000
TRADES_PER_DAY = 1
MOOD_HOUR = 16
BIG_MOVE = 300_000

# ----------------------------------------------------------------- 운세/그림/개미소리
ZODIAC = ["쥐", "소", "호랑이", "토끼", "용", "뱀", "말", "양", "원숭이", "닭", "개", "돼지"]
FORTUNE_HOUR = 6
FORTUNE_POSTS_PER_DAY = 3
ARTISTS = [n.strip() for n in os.getenv("PORING_ARTISTS", "쿠로베,마르실").split(",") if n.strip()]
DRAW_PER_DAY = int(os.getenv("PORING_DRAW_PER_DAY", "2"))
ANT_PER_DAY = int(os.getenv("PORING_ANT_PER_DAY", "3"))
OFFICE = worldcfg.OFFICES


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _counts(now: datetime) -> dict:
    d = _load(HOBBY_PATH)
    if d.get("date") != now.strftime("%Y-%m-%d"):
        d = {"date": now.strftime("%Y-%m-%d"), "trade": {}, "draw": {}, "ant": {}, "fortune_posted": []}
    return d


def _bump(now: datetime, kind: str, name: str) -> None:
    d = _counts(now)
    d.setdefault(kind, {})[name] = d.get(kind, {}).get(name, 0) + 1
    _save(HOBBY_PATH, d)


def _used(now: datetime, kind: str, name: str | None = None) -> int:
    c = _counts(now).get(kind, {})
    return c.get(name, 0) if name else sum(c.values())


# ===================================================================== 시세 받아오기
def _fetch_quote(ticker: str) -> dict | None:
    try:
        res = requests.get(YAHOO + ticker, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        meta = res.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or price)
        return {"price": price, "prev": prev, "currency": meta.get("currency", "KRW")}
    except Exception:  # noqa: BLE001
        return None


def _fetch_fx() -> dict:
    try:
        res = requests.get(FRANKFURTER, params={"from": "KRW", "to": ",".join(FX)}, timeout=TIMEOUT)
        rates = res.json().get("rates") or {}
        return {c: round(1 / float(r), 2) for c, r in rates.items() if r}  # 1 외화 = ?원
    except Exception:  # noqa: BLE001
        return {}


def refresh(now: datetime | None = None, quote=None, fx=None, fortune=None, agents: list[dict] | None = None) -> None:
    """[매시 회차 시작] 시세/환율(한 시간에 한 번)과 오늘 운세(하루 한 번)를 받아 둔다."""
    if not ENABLED:
        return
    now = now or now_kst()
    m = _load(MARKET_PATH)
    hk = now.strftime("%Y-%m-%d %H")
    if m.get("hour") != hk:
        q = quote or _fetch_quote
        prices = dict(m.get("prices") or {})
        got = 0
        for name, ticker in STOCKS.items():
            r = q(ticker)
            if r:
                prices[name] = r
                got += 1
        rates = (fx or _fetch_fx)() or m.get("fx") or {}
        m.update(hour=hk, prices=prices, fx=rates)
        _save(MARKET_PATH, m)
        print(f"[주식] 시세 {got}/{len(STOCKS)}종목, 환율 {len(rates)}개")
    if now.hour >= FORTUNE_HOUR:
        f = _load(FORTUNE_PATH)
        if f.get("date") != now.strftime("%Y-%m-%d"):
            fetch = fortune or _fetch_fortune
            animals = sorted({zodiac(a) for a in (agents or [])}) or ZODIAC
            texts = {z: t for z in animals if (t := fetch(z))}
            _save(FORTUNE_PATH, {"date": now.strftime("%Y-%m-%d"), "texts": texts})
            print(f"[운세] 오늘 운세 {len(texts)}개 띠")


def _fetch_fortune(animal: str) -> str:
    from core import fortune
    text = fortune._fetch_fortune_text(f"{animal}띠")
    if not text or text.startswith(("❌", "⚠️")):
        return ""
    return text.split("\n")[-1].strip()[:300]


def price_krw(name: str, m: dict | None = None) -> float | None:
    m = m if m is not None else _load(MARKET_PATH)
    p = (m.get("prices") or {}).get(name)
    if not p:
        return None
    if p.get("currency", "KRW") == "KRW":
        return p["price"]
    rate = (m.get("fx") or {}).get(p["currency"])
    return p["price"] * rate if rate else None


def holdings(name: str) -> dict:
    return ((_load(MARKET_PATH).get("portfolio") or {}).get(name)) or {}


def _pct(p: dict) -> float:
    return (p["price"] - p["prev"]) / p["prev"] * 100 if p.get("prev") else 0.0


def _market_line(m: dict, extra: list[str]) -> str:
    prices = m.get("prices") or {}
    names = list(dict.fromkeys([n for n in extra if n in prices] + list(prices)[:6]))
    parts = [f"{n} {price_krw(n, m):,.0f}원({_pct(prices[n]):+.1f}%)" for n in names if price_krw(n, m)]
    fx = m.get("fx") or {}
    fxp = [f"{c} {fx[c]:,.0f}원" if c != "JPY" else f"JPY(100엔) {fx[c] * 100:,.0f}원" for c in FX if c in fx]
    return " / ".join(parts) + (f" | 환율 {', '.join(fxp)}" if fxp else "")


def _investor(agent: dict) -> bool:
    return "경제" in news.interests(agent) or bool(holdings(agent["name"]))


# ===================================================================== 판단 프롬프트
def zodiac(agent: dict) -> str:
    z = str(agent.get("zodiac") or "").replace("띠", "")
    return z if z in ZODIAC else ZODIAC[sum(map(ord, agent.get("name", ""))) % 12]


def prompt_block(agent: dict, now: datetime | None = None) -> str:
    """판단 때 보는 것: 오늘 운세 + (투자자면) 시세/환율 + 내 주식."""
    if not ENABLED:
        return ""
    now = now or now_kst()
    out = []
    f = _load(FORTUNE_PATH)
    if f.get("date") == now.strftime("%Y-%m-%d") and (t := (f.get("texts") or {}).get(zodiac(agent))):
        out.append(f"[오늘의 운세 - 나는 {zodiac(agent)}띠, 아침에 봤다. 믿든 말든 성격대로] {t[:200]}")
    m = _load(MARKET_PATH)
    mine = holdings(agent["name"])
    if _investor(agent) and m.get("prices"):
        out.append(f"[왕도 상단 거래소 시세(실제 주식 시세) - 지금] {_market_line(m, list(mine))}")
    if mine:
        rows = []
        for n, h in mine.items():
            p = price_krw(n, m)
            if p:
                rows.append(f"{n} {h['shares']}주 (평단 {h['avg']:,.0f}원 → 지금 {p:,.0f}원, {(p / h['avg'] - 1) * 100:+.1f}%, "
                            f"{(p - h['avg']) * h['shares']:+,.0f}원)")
        if rows:
            out.append("[내 상단 지분(주식) - 이야기 속 돈으로 산 것]\n" + "\n".join(f"- {r}" for r in rows))
    return "\n".join(out)


def rules_and_spec(agent: dict, now: datetime | None = None) -> tuple[str, str]:
    """판단 규칙 + JSON 필드 (해당 없으면 빈 문자열)."""
    if not ENABLED:
        return "", ""
    now = now or now_kst()
    name = agent["name"]
    rules, spec = [], []
    if _investor(agent) and _load(MARKET_PATH).get("prices") and _used(now, "trade", name) < TRADES_PER_DAY:
        money = int(life.get(name).get("money", 0))
        rules.append(f"- 왕도 상단 거래소에서 실제 시세를 따르는 상단 지분(주식)을 사고팔 수 있다(이야기 속 돈 {money:,}원, 한 번에 최대 "
                     f"{min(MAX_BUY, int(money * MAX_BUY_RATIO)):,}원, 하루 한 번). 성격대로 - 대부분은 null.\n")
        spec.append(f', "stock": null 또는 {{"action": "매수|매도", "name": "{"|".join(STOCKS)}", '
                    '"amount": 매수할 금액(원, 매도면 0 - 전량 매도), "why": "왜 한 줄"}')
    if name in ARTISTS and _used(now, "draw", name) < 1 and _used(now, "draw") < DRAW_PER_DAY:
        rules.append("- 나는 그림을 그린다. 그리고 싶은 게 생기면 drawing에 (봇 /그림으로 실제 그림이 올라간다, 하루 한 장, 판타지 마을 풍경으로). "
                     "대부분은 null.\n")
        spec.append(', "drawing": null 또는 {"title": "그림 제목(한국어)", '
                    '"prompt": "이미지 생성용 영어 프롬프트 - 판타지 마을의 장면/화풍/분위기, 실존 인물 금지"}')
    if agent.get("company") in OFFICE and _used(now, "ant", name) < 1 and _used(now, "ant") < ANT_PER_DAY:
        rules.append("- 도서관/극장 일로 속이 터지면 /개미소리 짤로 통신방에 하소연할 수 있다 (하루 한 번, 대부분은 null).\n")
        spec.append(', "ant_voice": null 또는 "개미가 외칠 짧은 말(15자 이내)"')
    return "".join(rules), "".join(spec)


def parse(out: dict, agent: dict) -> dict:
    """판단 JSON에서 stock/drawing/ant_voice를 꺼낸다 (형식이 틀리면 버린다)."""
    res: dict = {}
    s = out.get("stock")
    if isinstance(s, dict) and s.get("name") in STOCKS and s.get("action") in ("매수", "매도"):
        try:
            amount = int(str(s.get("amount") or 0).replace(",", "").replace("원", ""))
        except ValueError:
            amount = 0
        res["stock"] = {"action": s["action"], "name": s["name"], "amount": amount, "why": str(s.get("why") or "")[:80]}
    d = out.get("drawing")
    if isinstance(d, dict) and str(d.get("prompt") or "").strip():
        res["drawing"] = {"title": str(d.get("title") or "무제").strip()[:40], "prompt": str(d["prompt"]).strip()[:400]}
    a = str(out.get("ant_voice") or "").strip()
    if a and a.lower() not in ("null", "none"):
        res["ant_voice"] = a[:20]
    return res


# ===================================================================== 실행
def trade(name: str, s: dict, now: datetime | None = None) -> dict | None:
    """매수/매도 한 번. 반환: {"command", "result", "change"(손익 - 매도만), "value"} 또는 None(못 함)."""
    now = now or now_kst()
    if _used(now, "trade", name) >= TRADES_PER_DAY:
        return None
    m = _load(MARKET_PATH)
    p = price_krw(s["name"], m)
    if not p:
        return None
    port = m.setdefault("portfolio", {}).setdefault(name, {})
    money = int(life.get(name).get("money", 0))
    if s["action"] == "매수":
        budget = min(max(0, s["amount"]), MAX_BUY, int(money * MAX_BUY_RATIO))
        shares = int(budget // p)
        if budget < MIN_BUY or shares < 1:
            return None
        cost = int(round(shares * p))
        h = port.get(s["name"]) or {"shares": 0, "avg": 0.0}
        h["avg"] = round((h["avg"] * h["shares"] + cost) / (h["shares"] + shares), 1)
        h["shares"] += shares
        port[s["name"]] = h
        left = life.give_money(name, -cost, f"주식 매수 {s['name']} {shares}주")
        result = f"체결: {s['name']} {shares}주 매수 × {p:,.0f}원 = {cost:,}원 | 남은 돈 {left:,}원"
        out = {"command": f"/주식 {s['name']} 매수 {shares}주", "result": result, "change": 0, "value": cost}
    else:
        h = port.get(s["name"])
        if not h:
            return None
        proceeds = int(round(h["shares"] * p))
        gain = int(round((p - h["avg"]) * h["shares"]))
        del port[s["name"]]
        left = life.give_money(name, proceeds, f"주식 매도 {s['name']} {h['shares']}주 ({gain:+,}원)")
        result = (f"체결: {s['name']} {h['shares']}주 전량 매도 × {p:,.0f}원 = {proceeds:,}원 "
                  f"(손익 {gain:+,}원) | 남은 돈 {left:,}원")
        out = {"command": f"/주식 {s['name']} 매도", "result": result, "change": gain, "value": proceeds}
    if not port:
        m["portfolio"].pop(name, None)
    _save(MARKET_PATH, m)
    _bump(now, "trade", name)
    print(f"[주식] {name} {out['command']} - {result}")
    return out


def daily_moods(now: datetime | None = None) -> list[tuple[str, int]]:
    """[장 마감 뒤 하루 한 번] 보유자별 오늘 평가손익 (이름, 원). 기분 변화는 여기서 반영한다."""
    now = now or now_kst()
    if not ENABLED or now.hour < MOOD_HOUR or now.weekday() >= 5:
        return []
    m = _load(MARKET_PATH)
    today = now.strftime("%Y-%m-%d")
    if m.get("mood_day") == today:
        return []
    m["mood_day"] = today
    _save(MARKET_PATH, m)
    out = []
    for name, port in (m.get("portfolio") or {}).items():
        delta = 0.0
        for n, h in port.items():
            p = (m.get("prices") or {}).get(n)
            cur = price_krw(n, m)
            if p and cur and p.get("prev"):
                delta += cur * (1 - p["prev"] / p["price"]) * h["shares"]
        delta = int(round(delta))
        if delta >= 50_000:
            life.apply_changes(name, {"happiness": 0.06 if delta < BIG_MOVE else 0.12}, reason=f"주식 오늘 {delta:+,}원")
        elif delta <= -50_000:
            life.apply_changes(name, {"stress": 0.06 if -delta < BIG_MOVE else 0.12}, reason=f"주식 오늘 {delta:+,}원")
        out.append((name, delta))
    return out


def fortune_posts(agents: list[dict], now: datetime | None = None) -> list[tuple[dict, str]]:
    """[아침] 오늘 /운세를 칠 몇 명과 그 운세 (하루 FORTUNE_POSTS_PER_DAY명, 깨어 있는 사람 중)."""
    now = now or now_kst()
    if not ENABLED or not (FORTUNE_HOUR + 1 <= now.hour <= 11):
        return []
    f = _load(FORTUNE_PATH)
    if f.get("date") != now.strftime("%Y-%m-%d"):
        return []
    c = _counts(now)
    left = FORTUNE_POSTS_PER_DAY - len(c.get("fortune_posted", []))
    if left <= 0:
        return []
    rnd = random.Random(f"fortune-{now:%Y-%m-%d-%H}")
    pool = [a for a in agents if a["name"] not in c["fortune_posted"] and (f.get("texts") or {}).get(zodiac(a))]
    pick = rnd.sample(pool, min(len(pool), 1 if left < FORTUNE_POSTS_PER_DAY else min(2, left)))
    c["fortune_posted"] += [a["name"] for a in pick]
    _save(HOBBY_PATH, c)
    return [(a, f["texts"][zodiac(a)]) for a in pick]


def use(now: datetime, kind: str, name: str) -> bool:
    """그림/개미소리 하루 횟수 확인 후 한 번 쓴다. 다 썼으면 False."""
    cap = DRAW_PER_DAY if kind == "draw" else ANT_PER_DAY
    if _used(now, kind, name) >= 1 or _used(now, kind) >= cap:
        return False
    _bump(now, kind, name)
    return True


def clean_prompt(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:400]
