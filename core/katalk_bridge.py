"""
카카오톡 연동 - 답장 전송만 담당한다 (수신은 디스코드 채널 릴레이 방식이라 core/kakao_relay.py가
닉네임을 파싱, 대화 로그 저장은 core/conversation_store.py로 옮겨감).
"""
from __future__ import annotations

import base64

import httpx

from config import settings


async def send_message(room_id: str, text: str) -> None:
    """봇의 답장을 브릿지 서버를 통해 실제 카톡방으로 내보낸다."""
    if not settings.KATALK_BRIDGE_URL:
        raise RuntimeError("KATALK_BRIDGE_URL이 설정되지 않았습니다.")
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{settings.KATALK_BRIDGE_URL}/send",
            json={"room_id": room_id, "text": text},
        )


async def send_image(room_id: str, image_bytes: bytes, filename: str = "image.webp") -> None:
    """
    이미지를 브릿지 서버를 통해 카톡방으로 내보낸다.
    [주의] 원본 봇은 base64 이미지를 별도 웹훅(katalk_webhook.send_katalk_image_webhook)으로
    보냈는데, 그 정확한 API 계약(엔드포인트 경로/payload 형식)은 확보하지 못해서 텍스트
    전송(send_message)과 비슷한 형태로 가정해 구현했다 - 실제 브릿지 서버 구현에 맞춰
    엔드포인트 경로나 payload 필드명을 조정해야 할 수 있다.
    """
    if not settings.KATALK_BRIDGE_URL:
        raise RuntimeError("KATALK_BRIDGE_URL이 설정되지 않았습니다.")
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            f"{settings.KATALK_BRIDGE_URL}/send_image",
            json={
                "room_id": room_id,
                "image_base64": base64.b64encode(image_bytes).decode("utf-8"),
                "filename": filename,
            },
        )

