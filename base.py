"""
백엔드는 전부 이 인터페이스만 구현하면 되고,
cogs/image_gen.py나 자율대화 쪽 호출부는 어떤 백엔드인지 몰라도 된다.
기존 봇에서 명령어용(horde_gen.py)과 자율대화용(aesun_img_gen.py)이 따로 구현되어
백엔드가 갈라져 있던 문제를 여기서 막는다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GeneratedImage:
    image_bytes: bytes
    backend: str
    model: str
    seed: str | None = None


class ImageGenError(RuntimeError):
    """백엔드 공통 에러 - 거부/타임아웃/HTTP 오류 등을 이걸로 감싸서 올린다."""


class ImageBackend:
    name: str = "base"

    async def generate(
        self,
        prompt: str,
        *,
        style: str | None = None,
        negative_prompt: str | None = None,
    ) -> GeneratedImage:
        raise NotImplementedError
