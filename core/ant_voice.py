"""
원본 ant_voice_gen.py를 그대로 이식 - 개미 이미지에 텍스트를 합성한다.
원본 소스 이미지/폰트 파일은 원본이 로컬(/mnt/discord_bot)에 미리 갖고 있던 에셋인데,
이 프로젝트엔 없어서 원본 저장소(이제 Public)에서 최초 1회 다운로드해 캐싱한다
(원본도 소스 이미지가 없으면 비슷하게 URL에서 내려받는 방식이었다).
"""
from __future__ import annotations

import io

import httpx
from PIL import Image, ImageDraw, ImageFont

from config import settings

ASSETS_DIR = settings.BASE_DIR / "storage" / "assets"
SOURCE_IMAGE_PATH = ASSETS_DIR / "ant_source.webp"
FONT_PATH = ASSETS_DIR / "NanumGothicCoding.ttf"

# 원본 저장소가 Public이라 raw로 원본 에셋을 그대로 받아온다.
SOURCE_IMAGE_URL = "https://raw.githubusercontent.com/yume-script/discord_bot/main/ant_source.webp"
FONT_URL = "https://raw.githubusercontent.com/yume-script/discord_bot/main/NanumGothicCoding.ttf"


async def _ensure_asset(path, url: str) -> bool:
    if path.exists():
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return False
        path.write_bytes(resp.content)
        return True


async def generate_ant_voice(text: str) -> bytes | None:
    """말풍선에 text를 합성한 이미지(WEBP bytes)를 반환. 에셋 확보 실패 시 None."""
    if not await _ensure_asset(SOURCE_IMAGE_PATH, SOURCE_IMAGE_URL):
        return None
    if not await _ensure_asset(FONT_PATH, FONT_URL):
        return None

    img = Image.open(SOURCE_IMAGE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT_PATH), 40)

    cx, cy = 210, 210
    lines = text.split("\\n")

    line_infos = []
    total_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        line_infos.append((line, w, h))
        total_h += h + 5

    current_y = cy - (total_h / 2)
    for line, w, h in line_infos:
        draw.text((cx - w / 2, current_y), line, font=font, fill="black")
        current_y += h + 5

    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=95)
    return buffer.getvalue()
