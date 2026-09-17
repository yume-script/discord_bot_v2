"""
[주의] 원본 satellite.py 소스를 확보하지 못했다 (저장소 파일 목록에서 링크를 못 찾음) -
새로 작성한 구현이다. 히마와리-9 실시간 위성사진(NICT 제공, https://himawari8.nict.go.jp,
무료/API 키 불필요 - himawaripy 등 여러 오픈소스 프로젝트가 쓰는 공개 API)을 가져온다.

[검증 안 됨] 이 도구의 샌드박스 네트워크 정책이 himawari8.nict.go.jp을 막고 있어서
실제 호출 테스트를 못 했다. 서버에 배포한 뒤 꼭 직접 확인할 것.
"""
from __future__ import annotations

import httpx

LATEST_URL = "https://himawari8.nict.go.jp/img/D531106/latest.json"
IMAGE_URL_TEMPLATE = "https://himawari8.nict.go.jp/img/D531106/1d/550/{date_path}/{time_str}_0_0.png"


async def get_satellite_image() -> bytes | None:
    """최신 히마와리 위성사진(PNG bytes)을 가져온다. 실패 시 None."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(LATEST_URL)
            if resp.status_code != 200:
                return None
            data = resp.json()
            date_str = data["date"]  # 예: "2026-09-17 05:20:00" (UTC)
            date_part, time_part = date_str.split(" ")
            y, m, d = date_part.split("-")
            hh, mm, ss = time_part.split(":")
            url = IMAGE_URL_TEMPLATE.format(date_path=f"{y}/{m}/{d}", time_str=f"{hh}{mm}{ss}")

            img_resp = await client.get(url)
            if img_resp.status_code != 200:
                return None
            return img_resp.content
        except Exception as e:  # noqa: BLE001
            print(f"[SATELLITE ERROR] {e}")
            return None
