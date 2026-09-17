"""
카카오톡 연동 - 답장 전송만 담당한다.

원본 katalk_webhook.py(discord_bot)를 확보해서 그대로 이식했다 - 브릿지 엔드포인트
("{KATALK_BRIDGE_URL}/reply", 원본 하드코딩 값과 동일: http://192.168.0.50:3000/reply)와
payload 형식({"type", "room", "data"})이 원본과 동일하다.

원본은 curl 서브프로세스(asyncio.to_thread + subprocess.run)로 쏘는데, 여기서는 프로젝트
전역에서 이미 쓰는 httpx로 같은 JSON POST를 보낸다 - 기능적으로 동일하고 의존성이 하나 준다.

원본은 인자로 "{이름} // {방ID} // {유저ID}" 형식의 avatar_name 전체를 받아서 room_id만
파싱해 썼는데(닉네임 앞부분은 콘솔 로그 출력에만 쓰임 - 실제 브릿지로 보내는 payload엔
room_id만 들어간다), 여기서는 UserRef가 이미 room_id를 갖고 있어서 곧바로 받는다.
"""
from __future__ import annotations

import base64

import httpx

from config import settings

_REPLY_PATH = "/reply"


async def send_message(room_id: str, text: str) -> None:
    """텍스트 메시지 전송. 원본 send_katalk_webhook과 동일한 payload."""
    if not settings.KATALK_BRIDGE_URL:
        raise RuntimeError("KATALK_BRIDGE_URL이 설정되지 않았습니다.")
    payload = {"type": "text", "room": str(room_id), "data": text}
    async with httpx.AsyncClient(timeout=60) as client:
        await client.post(f"{settings.KATALK_BRIDGE_URL}{_REPLY_PATH}", json=payload)


async def send_image(room_id: str, image_bytes: bytes, filename: str = "image.webp") -> None:
    """이미지 전송. 원본 send_katalk_image_webhook과 동일한 payload(base64 문자열)."""
    if not settings.KATALK_BRIDGE_URL:
        raise RuntimeError("KATALK_BRIDGE_URL이 설정되지 않았습니다.")
    base64_data = base64.b64encode(image_bytes).decode("utf-8")
    payload = {"type": "image", "room": str(room_id), "data": base64_data}
    async with httpx.AsyncClient(timeout=60) as client:
        await client.post(f"{settings.KATALK_BRIDGE_URL}{_REPLY_PATH}", json=payload)
