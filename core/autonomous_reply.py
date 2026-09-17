"""
기존 봇(app.py의 _handle_auto_response / _should_skip, app_chat_handler.py의 호출어 감지)을
그대로 이식한 모듈. 로직(호출어 3종, 쿨다운 60초, 확률 3%)은 변경하지 않았다.

기존 봇에서 last_aesun_active_time과 active_channels는 봇 인스턴스 전역에 있던 단일 상태였고,
카톡 메시지든 디스코드 메시지든 같은 on_message 파이프라인을 탔기 때문에 자연히 공유됐다.
새 봇도 카톡이 디스코드 채널 릴레이 방식이라 같은 on_message(cogs/chat.py)를 타므로,
이 모듈이 곧 전역 공유 지점이다 - "카톡/디스코드 공용" 결정 그대로 반영된다.
"""
from __future__ import annotations

import random
import time

from config import settings

# 명시적 호출어 - 감지되면 확률/쿨다운 계산 없이 무조건 응답 (원본의 trigger_words 자리 -
# 원본은 "애순아/애순이/애순"이었는데, 이 봇의 페르소나가 "아메하나"로 바뀌면서 교체했다.
# "하나만"/"하나 주세요" 같은 무관한 대화까지 반응하는 오탐이 있어서, "하나야" 하나로만 좁혔다.
CALL_TRIGGER_WORDS = ["하나야"]

_last_active_time: float = 0.0
_active_keys: set[str] = set()


def detect_call_word(text: str) -> str | None:
    """호출어가 포함되어 있으면 그 단어를, 없으면 None을 반환."""
    return next((w for w in CALL_TRIGGER_WORDS if w in text), None)


def strip_call_word(text: str) -> str:
    """호출어를 제거하고 앞뒤 문장부호/공백을 정리 (app_chat_handler.py와 동일)."""
    prompt = text
    for w in CALL_TRIGGER_WORDS:
        prompt = prompt.replace(w, "")
    return prompt.strip().lstrip(". ").rstrip(". ")


def mark_active(key: str) -> None:
    """응답 생성을 시작할 때 호출 (기존 bot.active_channels.add)."""
    _active_keys.add(key)


def clear_active(key: str) -> None:
    """응답 생성이 끝나면 반드시 호출 (기존 bot.active_channels.discard) - try/finally로 감쌀 것."""
    _active_keys.discard(key)


def is_active(key: str) -> bool:
    """이미 이 key(채널/방)에서 응답을 생성 중인지 (기존 _should_skip 조건 1)."""
    return key in _active_keys


def should_auto_reply(text: str) -> bool:
    """
    호출어가 없는 일반 메시지에 대해 확률적으로 참견할지 결정한다 (기존 _handle_auto_response).

    - AUTO_REPLY_TRIGGER_KEYWORDS(기본 "아메하나,똑똑,안녕") 중 하나라도 포함되면
      쿨다운/확률 계산을 건너뛰고 바로 응답 시도.
    - 아니면 마지막 자율 응답 이후 AUTO_REPLY_COOLDOWN_SEC(기본 60초)가 지나야 하고,
      그 후에도 AUTO_REPLY_PROBABILITY(기본 3%) 확률을 통과해야 응답.
    - last_active_time은 카톡/디스코드 구분 없이 이 모듈 하나에 공유된다 (기존과 동일).
    """
    global _last_active_time
    now = time.time()

    is_explicit = any(k in text for k in settings.AUTO_REPLY_TRIGGER_KEYWORDS)
    if not is_explicit:
        if (now - _last_active_time) < settings.AUTO_REPLY_COOLDOWN_SEC:
            return False
        if random.random() > settings.AUTO_REPLY_PROBABILITY:
            return False

    _last_active_time = now
    return True
