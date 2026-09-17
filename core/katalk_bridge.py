"""
카카오톡 연동 - 답장 전송만 담당한다 (수신은 디스코드 채널 릴레이 방식이라 core/kakao_relay.py가
닉네임을 파싱, 대화 로그 저장은 core/conversation_store.py로 옮겨감).
"""
from __future__ import annotations

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
