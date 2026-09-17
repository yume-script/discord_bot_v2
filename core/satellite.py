"""
원본 satellite.py(discord_bot)를 확보해서 그대로 이식 - 기상청(KMA) 천리안 2A호 위성 영상을
가져온다. 이전에 원본을 못 구해서 히마와리 공개 API로 추측 구현했던 버전은 폐기하고 교체했다.
"""
from __future__ import annotations

import httpx

IMAGE_LIST_URL = "https://www.weather.go.kr/w/wnuri-img/rest/sat/images/gk2a.do?mapType=img&area=ko020lc&itv=0.5"
BASE_IMAGE_URL = "https://www.weather.go.kr"


async def get_satellite_image() -> tuple[bytes, str] | None:
    """(이미지 bytes, 관측시간 문자열)을 반환. 실패 시 None."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(IMAGE_LIST_URL)
            if resp.status_code != 200:
                return None
            image_info_list = resp.json()
            if not image_info_list:
                return None

            latest_info = image_info_list[-1]
            target_url = BASE_IMAGE_URL + latest_info["url"]
            obs_time = latest_info.get("tm", "알 수 없는 시간")

            img_resp = await client.get(target_url)
            if img_resp.status_code != 200:
                return None
            return img_resp.content, obs_time
        except Exception as e:  # noqa: BLE001 - 원본과 동일하게 어떤 예외든 흡수
            print(f"[SATELLITE ERROR] {e}")
            return None
