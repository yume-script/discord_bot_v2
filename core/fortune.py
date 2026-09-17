"""
원본 fortune.py를 그대로 이식 - 네이버 검색 결과에서 "{질의} 운세" 텍스트를 스크레이핑한다.
스크레이핑 자체는 동기 함수(requests+BeautifulSoup)라 원본과 동일하게 스레드풀에서 돌린다.
"""
from __future__ import annotations

import asyncio

import requests
from bs4 import BeautifulSoup

DIVIDER = "━━━━━━━━━━━━━━━━"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    )
}


def _fetch_fortune_text(query: str) -> str:
    search_query = query + " 운세"
    url = f"https://search.naver.com/search.naver?query={search_query}"
    try:
        response = requests.get(url, headers=_HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")
        result_element = soup.select_one(".detail p") or soup.select_one(
            "#yearFortune .detail > p:nth-child(3)"
        )
        if result_element:
            result_text = result_element.get_text(strip=True)
            blank = "\u200b" * 500
            return f"🌟 **오늘의 {query} 운세** 🌟\n{DIVIDER}\n{blank}\n{result_text}"
        return f"❌ '{query}'에 대한 정보를 찾을 수 없습니다.\n(예: /운세 양띠, /운세 물병자리)"
    except Exception as e:  # noqa: BLE001 - 원본과 동일하게 어떤 예외든 사용자 메시지로 흡수
        print(f"[FORTUNE ERROR] {e}")
        return "⚠️ 운세 정보를 가져오는 중 오류가 발생했습니다."


async def get_fortune(query: str) -> str:
    """query가 비어있으면 사용법 안내를 돌려준다 (원본과 동일한 문구)."""
    query = query.strip()
    if not query:
        return "💡 사용법: `/운세 [띠 또는 별자리]`\n(예: `/운세 양띠`, `/운세 사자자리`)"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _fetch_fortune_text, query)


def to_kakao_text(discord_text: str) -> str:
    """디스코드 마크다운(**bold**)을 걷어내고 카톡용으로 다듬는다 (원본과 동일)."""
    k_msg = discord_text.replace("**", "")
    if "오늘의" in k_msg:
        k_msg += f"\n{DIVIDER}"
    return k_msg
