"""
날씨/환율/주식 조회 도구. LangChain @tool로 감싸서 두 곳에서 그대로 재사용한다:
  1) cogs/lookup.py의 슬래시/텍스트 명령어(/날씨, /전국날씨, /환율, /주식)
  2) ai/rag_engine.py의 LLM 도구 호출 - 애순이에게 자연어로 "환율 알려줘" 물어봐도
     LLM이 스스로 이 도구를 호출해서 실제 데이터로 답하게 된다.

명령어와 자연어 대화가 서로 다른 구현을 쓰면 답이 어긋날 수 있어서, 데이터 소스를
이 파일 하나로 통일했다.

원본 봇의 weather_district.py/weather_nation.py/exchange.py/stock.py 원본 소스는
확보하지 못해서(레포에서 확인은 했지만 파일 목록 링크에는 안 걸림), 여기는 새로
작성한 구현이다. 키가 필요 없는 공개 API를 사용했다:
  - 날씨: Open-Meteo (무료, API 키 불필요)
  - 환율: Frankfurter (ECB 기준, 무료, API 키 불필요)
  - 주식: Yahoo Finance 비공식 차트 API (무료, API 키 불필요)
"""
from __future__ import annotations

import httpx
from langchain_core.tools import tool

FRANKFURTER_BASE = "https://api.frankfurter.app"
OPEN_METEO_GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
YAHOO_FINANCE_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"

WEATHER_CODE_KR = {
    0: "맑음", 1: "대체로 맑음", 2: "부분적으로 흐림", 3: "흐림",
    45: "안개", 48: "서리 안개",
    51: "이슬비 약함", 53: "이슬비", 55: "이슬비 강함",
    61: "비 약함", 63: "비", 65: "비 강함",
    71: "눈 약함", 73: "눈", 75: "눈 강함",
    80: "소나기 약함", 81: "소나기", 82: "소나기 강함",
    95: "뇌우", 96: "뇌우(우박 약함)", 99: "뇌우(우박 강함)",
}


async def _geocode(location: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            OPEN_METEO_GEOCODE,
            params={"name": location, "count": 1, "language": "ko", "country": "KR"},
        )
        data = resp.json()
        results = data.get("results")
        return results[0] if results else None


@tool
async def get_weather(location: str) -> str:
    """특정 지역(도시/동네 이름)의 현재 날씨와 오늘 최고/최저 기온을 알려준다. 예: '서울', '부산 해운대'."""
    place = await _geocode(location)
    if not place:
        return f"'{location}' 지역을 찾을 수 없어요."

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            OPEN_METEO_FORECAST,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min",
                "timezone": "Asia/Seoul",
            },
        )
        data = resp.json()

    current = data.get("current", {})
    daily = data.get("daily", {})
    temp = current.get("temperature_2m")
    code = current.get("weather_code")
    condition = WEATHER_CODE_KR.get(code, "알 수 없음")
    tmax_list = daily.get("temperature_2m_max") or [None]
    tmin_list = daily.get("temperature_2m_min") or [None]

    name = place.get("name", location)
    return f"{name} 현재 {temp}°C, {condition}. 오늘 최고 {tmax_list[0]}°C / 최저 {tmin_list[0]}°C"


@tool
async def get_nationwide_weather() -> str:
    """서울/부산/대구/인천/광주/대전/제주 등 전국 주요 도시의 현재 날씨를 한번에 알려준다."""
    cities = ["서울", "부산", "대구", "인천", "광주", "대전", "제주"]
    lines = []
    for city in cities:
        try:
            lines.append(await get_weather.ainvoke({"location": city}))
        except Exception:
            lines.append(f"{city}: 조회 실패")
    return "\n".join(lines)


@tool
async def get_exchange_rate(currency: str = "USD") -> str:
    """원화(KRW) 기준 환율을 알려준다. currency는 USD, JPY, EUR, CNY 같은 통화 코드."""
    currency = currency.upper().strip()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{FRANKFURTER_BASE}/latest", params={"from": currency, "to": "KRW"})
        if resp.status_code != 200:
            return f"{currency} 환율 조회 실패"
        data = resp.json()
    rate = data.get("rates", {}).get("KRW")
    if rate is None:
        return f"{currency}는 지원하지 않는 통화 코드예요."
    return f"1 {currency} = {rate:,.2f} KRW (기준일: {data.get('date')})"


@tool
async def get_stock_price(ticker: str) -> str:
    """
    주식 현재가를 알려준다. 한국 주식은 '005930.KS'(삼성전자)처럼 종목코드+.KS(코스피)
    또는 .KQ(코스닥), 미국 주식은 'AAPL'처럼 티커로 입력.
    """
    headers = {"User-Agent": "Mozilla/5.0"}
    async with httpx.AsyncClient(timeout=10, headers=headers) as client:
        resp = await client.get(f"{YAHOO_FINANCE_CHART}/{ticker}")
        if resp.status_code != 200:
            return f"'{ticker}' 시세 조회 실패"
        data = resp.json()

    try:
        result = data["chart"]["result"][0]
        meta = result["meta"]
        price = meta["regularMarketPrice"]
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        currency = meta.get("currency", "")
        name = meta.get("symbol", ticker)
        if prev_close:
            change = price - prev_close
            pct = change / prev_close * 100
            return f"{name}: {price:,.2f} {currency} ({change:+,.2f}, {pct:+.2f}%)"
        return f"{name}: {price:,.2f} {currency}"
    except (KeyError, IndexError, TypeError):
        return f"'{ticker}' 시세 정보를 해석할 수 없어요. 코드가 맞는지 확인해주세요 (예: 005930.KS, AAPL)."


LOCAL_TOOLS = [get_weather, get_nationwide_weather, get_exchange_rate, get_stock_price]
