"""
카카오톡 연동은 새 봇에서도 유지. 메시지 수신은 디스코드 채널 릴레이 방식(원본 봇과 동일 -
core/kakao_relay.py가 닉네임을 파싱)이라 이 모듈은 로그 저장 + 답장 전송만 담당한다.

카톡 로그 저장은 기존 봇에서 여러 파일에 중복 구현되어 있던 문제(katalk_log_utils.py로
나중에야 통합)가 있었으므로, 새 봇은 처음부터 이 모듈 하나만 저장 책임을 지도록 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from config import settings
from core.user_ref import UserRef

settings.KATALK_LOG_DIR.mkdir(parents=True, exist_ok=True)


async def send_message(room_id: str, text: str) -> None:
    """봇의 답장을 브릿지 서버를 통해 실제 카톡방으로 내보낸다 (기존 katalk_webhook.send_katalk_webhook 역할)."""
    if not settings.KATALK_BRIDGE_URL:
        raise RuntimeError("KATALK_BRIDGE_URL이 설정되지 않았습니다.")
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{settings.KATALK_BRIDGE_URL}/send",
            json={"room_id": room_id, "text": text},
        )


def log_message(user: UserRef, text: str, *, direction: str = "in") -> None:
    """방(room_id) 단위 로그 파일에 JSONL로 append. 단일 저장 지점 - 다른 모듈은 이걸 호출만 한다."""
    room_id = user.raw_id.split("//", 1)[0]
    log_path = settings.KATALK_LOG_DIR / f"{room_id}.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user.key,
        "direction": direction,
        "text": text,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
