"""
cogs/image_gen.py(명령어)든 자율대화 쪽이든, 이미지가 필요하면 전부 이 모듈의
generate_image()만 호출한다. 기존 봇에서 horde_gen.py(명령어용)와
aesun_img_gen.py(자율대화용)가 따로 구현되어 있던 중복을 여기서 원천 차단한다.
"""
from __future__ import annotations

from ai.backends.base import GeneratedImage, ImageBackend
from ai.backends.horde_backend import HordeBackend
from config import settings

_BACKENDS: dict[str, type[ImageBackend]] = {
    "horde": HordeBackend,
}

_instance_cache: dict[str, ImageBackend] = {}


def _get_backend(name: str) -> ImageBackend:
    if name not in _instance_cache:
        backend_cls = _BACKENDS.get(name)
        if backend_cls is None:
            raise ValueError(f"알 수 없는 이미지 생성 백엔드: {name}")
        _instance_cache[name] = backend_cls()
    return _instance_cache[name]


async def generate_image(
    prompt: str,
    *,
    style: str | None = None,
    negative_prompt: str | None = None,
    backend_name: str | None = None,
) -> GeneratedImage:
    """
    backend_name을 안 주면 config의 IMAGE_GEN_BACKEND(기본값)를 쓴다.
    /그림스타일처럼 사용자가 명시적으로 백엔드를 고르는 명령에서만 backend_name을 넘긴다.
    """
    name = backend_name or settings.IMAGE_GEN_BACKEND
    backend = _get_backend(name)
    return await backend.generate(prompt, style=style, negative_prompt=negative_prompt)
