"""
브릿지 스크립트(카톡 -> 디스코드 웹훅 릴레이)가 디스코드 메시지 작성자 이름을
"{발신자명}//{방ID}//{유저ID}" 형식으로 인코딩해서 보낸다 (구분자 "//", 공백 없음).
이 모듈은 그걸 다시 파싱하는 부분만 담당한다.

발신자명 자체에 우연히 구분자와 같은 문자열이 섞여 있을 극단적 경우까지 고려해서,
앞이 아니라 뒤에서부터 최대 2번만 자른다(rsplit) - 방ID/유저ID는 항상 안전하게 분리되고,
그 앞부분 전체가 발신자명이 된다.
"""
from __future__ import annotations

from config import settings
from core.user_ref import UserRef


def parse_kakao_author(name_string: str) -> UserRef | None:
    """
    "{발신자명}//{방ID}//{유저ID}" 형식이면 UserRef(카톡)를, 아니면 None을 반환.
    구분자는 NICKNAME_DELIMITER(기본 "//").
    """
    delimiter = settings.NICKNAME_DELIMITER
    parts = name_string.rsplit(delimiter, 2)
    if len(parts) != 3:
        return None

    kakao_name, room_id, user_id = (p.strip() for p in parts)
    if not room_id or not user_id:
        return None

    return UserRef.from_kakao(room_id, user_id, display_name=kakao_name)
