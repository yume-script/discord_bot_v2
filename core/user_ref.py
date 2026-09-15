"""
카톡 발신자(방ID//회원번호)와 디스코드 발신자(author.id)는 형태가 달라서
그대로 동시성 키나 로그 키로 쓰면 충돌/혼선이 생긴다 (예: 기존 봇의 /그림 명령 동시요청 버그).

UserRef는 둘을 하나의 값 객체로 감싸서, 그 위의 코드(cogs, concurrency, 로그 저장)가
"카톡인지 디스코드인지"를 몰라도 되게 만드는 게 목적이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Channel(str, Enum):
    DISCORD = "discord"
    KAKAO = "kakao"


@dataclass(frozen=True, slots=True)
class UserRef:
    channel: Channel
    # discord: author.id (문자열화)
    # kakao: f"{room_id}//{member_no}"
    raw_id: str
    display_name: str | None = None

    @property
    def key(self) -> str:
        """동시성 큐, 로그 파일명 등에 쓰는 전역 유일 키."""
        return f"{self.channel.value}:{self.raw_id}"

    @classmethod
    def from_discord(cls, author_id: int, display_name: str | None = None) -> "UserRef":
        return cls(Channel.DISCORD, str(author_id), display_name)

    @classmethod
    def from_kakao(cls, room_id: str, member_no: str, display_name: str | None = None) -> "UserRef":
        return cls(Channel.KAKAO, f"{room_id}//{member_no}", display_name)

    def __str__(self) -> str:  # 로그 출력용
        return self.key
