"""
원본 ant_voice_gen.py를 그대로 이식 - 개미 이미지에 텍스트를 합성한다.
원본 소스 이미지/폰트 파일은 원본이 로컬(/mnt/discord_bot)에 미리 갖고 있던 에셋인데,
이 프로젝트엔 없어서 원본 저장소(이제 Public)에서 최초 1회 다운로드해 캐싱한다
(원본도 소스 이미지가 없으면 비슷하게 URL에서 내려받는 방식이었다).
"""
from __future__ import annotations

import asyncio
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
    # PIL 합성/인코딩은 CPU를 쓰는 동기 작업이라 이벤트 루프를 막지 않게 스레드에서 돌린다.
    return await asyncio.to_thread(_render, text)


# 말풍선 안쪽(원본 이미지 687x684 기준)에 글이 들어갈 칸. 예전엔 40pt 한 줄로 (210, 210)에 가운데 정렬만 해서
# 글이 길면 말풍선 밖으로 삐져나가고 왼쪽 끝 글자가 잘렸다 - 이제 칸 안에 들어가도록 줄을 나누고 글자 크기를 줄인다.
BOX_CX, BOX_CY = 212, 205
BOX_W, BOX_H = 215, 150
FONT_SIZES = (44, 40, 36, 32, 28, 25, 22, 20, 18)
LINE_GAP = 6


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """한 문단을 max_w 안에 들어가게 나눈다 - 띄어쓰기 단위로, 한 단어가 너무 길면 글자 단위로."""
    def width(t: str) -> float:
        return draw.textlength(t, font=font)

    lines, cur = [], ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if width(cand) <= max_w:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        cur = ""
        for ch in word:  # 단어 하나가 칸보다 길면 글자 단위로 자른다
            if width(cur + ch) > max_w and cur:
                lines.append(cur)
                cur = ""
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]


def _layout(draw, text: str) -> tuple:
    """(폰트, 줄 목록, 줄 높이) - 말풍선 칸에 들어가는 가장 큰 글자 크기로."""
    paras = [p.strip() for p in text.replace("\\n", "\n").split("\n")]
    paras = [p for p in paras if p] or [text.strip()]
    for size in FONT_SIZES:
        font = ImageFont.truetype(str(FONT_PATH), size)
        lines = [ln for para in paras for ln in _wrap(draw, para, font, BOX_W)]
        bbox = draw.textbbox((0, 0), "가Ag", font=font)
        line_h = bbox[3] - bbox[1]
        total_h = len(lines) * line_h + (len(lines) - 1) * LINE_GAP
        if total_h <= BOX_H and all(draw.textlength(ln, font=font) <= BOX_W for ln in lines):
            return font, lines, line_h
    # 가장 작은 글자로도 넘치면 들어가는 줄까지만 (끝에 …)
    max_lines = max(1, (BOX_H + LINE_GAP) // (line_h + LINE_GAP))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "…", font=font) > BOX_W:
            last = last[:-1]
        lines[-1] = last + "…"
    return font, lines, line_h


def _render(text: str) -> bytes:
    img = Image.open(SOURCE_IMAGE_PATH).convert("RGB")
    draw = ImageDraw.Draw(img)
    font, lines, line_h = _layout(draw, text)
    total_h = len(lines) * line_h + (len(lines) - 1) * LINE_GAP
    top = BOX_CY - total_h / 2
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        y = top + i * (line_h + LINE_GAP)
        # textbbox 위쪽 여백(폰트마다 다름)을 빼서 실제 글자가 칸 가운데에 오게
        off = draw.textbbox((0, 0), line or "가", font=font)[1]
        draw.text((BOX_CX - w / 2, y - off), line, font=font, fill="black")

    buffer = io.BytesIO()
    img.save(buffer, format="WEBP", quality=95)
    return buffer.getvalue()
