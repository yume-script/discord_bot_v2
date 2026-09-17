"""
원본 봇의 app_kakao_handler.py 중 피드 메시지(입장/퇴장) 처리 부분을 그대로 이식.
카톡 브릿지는 입장/퇴장 이벤트를 content가 '{"feedType"...'로 시작하는 JSON 문자열
메시지로 보낸다 (feedType 4 = 입장, 2 = 퇴장). 로직/메시지 문구는 원본과 동일하게 유지했다.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger("kakao_feed")

FEED_PREFIX = '{"feedType"'


def is_feed_message(content: str) -> bool:
    return content.startswith(FEED_PREFIX)


def build_feed_reply(content: str) -> str | None:
    """
    피드 메시지를 파싱해서 응답 문구를 만든다. 입장/퇴장이 아니거나 파싱 실패 시 None.
    (원본 handle_kakao_features의 '1. 입장/퇴장 피드 메시지 처리' 블록 그대로)
    """
    try:
        data = json.loads(content)
        feed_type = data.get("feedType")

        if feed_type == 4:  # 입장
            nick = data["members"][0]["nickName"]
            return f"✨ **{nick}**님, 환영합니다! 🎉"
        elif feed_type == 2:  # 퇴장
            nick = data["member"]["nickName"]
            return f"👋 **{nick}**님이 채팅방을 나갔습니다."
        else:
            return None
    except Exception:
        log.exception("피드 메시지 파싱 실패")
        return None
