"""
관리자 확인을 기다리는 "서버에 영향을 주는" 도구 호출 보관소.

LLM이 위험한 도구(core/tool_policy.is_server_affecting - 컨테이너 정지, 파일 쓰기, 데이터
삭제 등)를 부르면, 관리자가 요청한 대화라도 바로 실행하지 않고 여기에 담아둔다
(ai/rag_engine.py). 관리자가 같은 채널에서 CONFIRM_TTL_SEC 안에 "확인"이라고 답해야
cogs/chat.py가 꺼내서 실제로 실행한다.

왜 필요한가: 관리자 대화에서도 LLM은 유튜브 자막, Plex 메타데이터, 누구나 쓸 수 있는
메모리 같은 외부 텍스트를 읽는다. 그 안에 "컨테이너를 지워라" 같은 지시가 섞여 있으면
LLM이 그대로 따를 수 있어서, 사람이 실행 직전에 내용을 보고 승인하게 한다.

scope는 "{채널ID}:{관리자 유저ID}" - 다른 사람의 "확인"이나 다른 채널의 "확인"으로는
실행되지 않는다. 프로세스 메모리에만 두므로 봇이 재시작되면 대기 중인 작업은 사라진다
(안전한 쪽으로 실패).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

CONFIRM_TTL_SEC = 180
CONFIRM_WORDS = ("확인", "실행")
CANCEL_WORDS = ("취소",)


@dataclass
class PendingCall:
    tool: Any  # LangChain 도구 객체 (ainvoke 가능)
    args: dict

    def describe(self) -> str:
        args = json.dumps(self.args, ensure_ascii=False)
        if len(args) > 300:
            args = args[:300] + "…"
        return f"`{self.tool.name}` {args}"


@dataclass
class _Entry:
    calls: list[PendingCall] = field(default_factory=list)
    expires_at: float = 0.0


_pending: dict[str, _Entry] = {}


def make_scope(channel_id: int | str, user_id: int | str) -> str:
    return f"{channel_id}:{user_id}"


def add(scope: str, tool, args: dict) -> None:
    """확인 대기 목록에 추가. 같은 scope에 이미 대기 중인 게 있으면 이어 붙이고 만료 시각을 갱신."""
    entry = _pending.get(scope)
    now = time.time()
    if entry is None or entry.expires_at < now:
        entry = _Entry()
        _pending[scope] = entry
    entry.calls.append(PendingCall(tool=tool, args=dict(args or {})))
    entry.expires_at = now + CONFIRM_TTL_SEC


def peek(scope: str) -> list[PendingCall]:
    entry = _pending.get(scope)
    if entry is None:
        return []
    if entry.expires_at < time.time():
        _pending.pop(scope, None)
        return []
    return list(entry.calls)


def pop(scope: str) -> list[PendingCall]:
    """대기 중인 호출을 꺼내고 목록에서 지운다. 만료됐으면 빈 리스트."""
    calls = peek(scope)
    _pending.pop(scope, None)
    return calls


def is_confirm(text: str) -> bool:
    return text.strip().rstrip(".!") in CONFIRM_WORDS


def is_cancel(text: str) -> bool:
    return text.strip().rstrip(".!") in CANCEL_WORDS
