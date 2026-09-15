"""
기존 봇(app.py)은 별도 수신 서버 없이, 브릿지가 카톡 메시지를 "카톡이름//방ID//유저ID"
형식의 닉네임으로 인코딩해서 지정된 디스코드 채널(KATALK_LINKED_CHANNEL_IDS, 기존
TARGET_THREAD_IDS)에 일반 메시지로 올려주는 방식이었다. 이 모듈은 message.author.name을
그 형식으로 파싱하는 부분만 담당한다 (기존 app.py의 [1] 사용자 정보 구조화 로직 이식).
"""
from __future__ import annotations

from config import settings
from core.user_ref import UserRef


def parse_kakao_author(name_string: str) -> UserRef | None:
    """
    "카톡이름//방ID//유저ID" 형식이면 UserRef(카톡)를, 아니면 None을 반환.
    구분자는 NICKNAME_DELIMITER(기본 "//").
    """
    delimiter = settings.NICKNAME_DELIMITER
    parts = [p.strip() for p in name_string.split(delimiter)]
    if len(parts) < 3:
        return None

    kakao_name, room_id, user_id = parts[0], parts[1], parts[2]
    return UserRef.from_kakao(room_id, user_id, display_name=kakao_name)
