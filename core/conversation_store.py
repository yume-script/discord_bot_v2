"""
대화 기록을 SQLite에 저장하고, 아메하나가 답할 때 최근 대화를 맥락으로 참고할 수 있게 꺼내온다.
카톡방/디스코드 채널 구분 없이 conversation_key 하나로 다룬다
(카톡 = room_id, 디스코드 = f"discord:{channel.id}").

처음엔 방별 JSONL 파일에 무한정 append하는 방식이었는데, "최근 N개 메시지"를 가져오려면
매번 파일을 끝까지 읽어야 하는 게 문제였다. SQLite로 바꾸면 인덱스 조회 한 번으로 끝나고,
나중에 오래된 데이터를 정리하고 싶어지면 DELETE 한 줄로 가능하다.

동시성: SQLite 자체가 파일 잠금으로 다중 접근을 처리해주고, 여기서는 프로세스 내부에서도
겹쳐 쓰기가 없도록 threading.Lock으로 감싼다 (동기 I/O라 asyncio 이벤트루프 입장에서도
한 번에 하나씩만 실행됨).
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from config import settings

# [변경] 원래 datetime.now(timezone.utc)로 UTC 시각을 저장했는데, 서버 시스템 시각이
# UTC라 실제 카톡/디스코드에서 보낸 시각과 9시간 차이가 나는 버그가 있었다 (예: 11:46에
# 보낸 메시지가 DB엔 02:46으로 찍힘). 한국 서비스라 KST(UTC+9)로 고정해서 저장한다 -
# 한국은 서머타임이 없어서 이 고정 오프셋으로 충분하다.
KST = timezone(timedelta(hours=9))

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    settings.CONVERSATION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.CONVERSATION_DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_key TEXT NOT NULL,
            display_name TEXT,
            direction TEXT NOT NULL,
            text TEXT NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_key, id)"
    )
    return conn


def log_message(conversation_key: str, display_name: str | None, text: str, *, direction: str = "in") -> None:
    """direction: 'in'(유저가 보낸 메시지) | 'out'(봇이 보낸 답장)."""
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO messages (conversation_key, display_name, direction, text, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (conversation_key, display_name, direction, text, datetime.now(KST).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()


def get_recent_context(conversation_key: str, limit: int | None = None) -> list[tuple[str | None, str, str]]:
    """
    최근 메시지를 오래된 순서로 반환: [(display_name, text, direction), ...].
    limit 생략 시 settings.CONVERSATION_CONTEXT_LIMIT 사용.
    """
    limit = limit if limit is not None else settings.CONVERSATION_CONTEXT_LIMIT
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT display_name, text, direction FROM messages "
                "WHERE conversation_key = ? ORDER BY id DESC LIMIT ?",
                (conversation_key, limit),
            ).fetchall()
        finally:
            conn.close()
    return list(reversed(rows))


def get_messages_by_display_name(conversation_key: str, display_name: str, limit: int = 100) -> list[str]:
    """
    같은 방(conversation_key) 안에서 특정 유저(display_name)가 보낸 최근 메시지 텍스트만 뽑는다
    (/mbti의 대화 로그 기반 분석용 - 원본 mbti_system.py가 katalk_log JSONL에서 하던 걸
    여기서는 SQLite 조회로 한다). 오래된 순서로 반환.
    """
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT text FROM messages "
                "WHERE conversation_key = ? AND display_name = ? AND direction = 'in' "
                "ORDER BY id DESC LIMIT ?",
                (conversation_key, display_name, limit),
            ).fetchall()
        finally:
            conn.close()
    return [r[0] for r in reversed(rows)]


def prune_older_than(days: int) -> int:
    """
    오래된 대화 기록을 정리하고 싶어지면 쓸 수 있는 함수 (지금은 자동으로 호출되지 않음 -
    필요해지면 스케줄러/cron에 연결). 삭제된 행 수를 반환한다.
    """
    cutoff = (datetime.now(KST) - timedelta(days=days)).isoformat()
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def get_message_counts_by_user(conversation_key: str, since_iso: str | None = None) -> list[tuple[str, int]]:
    """
    [신규] /카톡순위, /월간카톡용 - 같은 방 안에서 유저(direction='in')별 메시지 건수를
    많은 순으로 반환한다. since_iso를 주면 그 시각 이후만 집계한다.
    """
    with _lock:
        conn = _connect()
        try:
            if since_iso:
                rows = conn.execute(
                    "SELECT display_name, COUNT(*) FROM messages "
                    "WHERE conversation_key = ? AND direction = 'in' AND display_name IS NOT NULL "
                    "AND ts >= ? GROUP BY display_name ORDER BY COUNT(*) DESC",
                    (conversation_key, since_iso),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT display_name, COUNT(*) FROM messages "
                    "WHERE conversation_key = ? AND direction = 'in' AND display_name IS NOT NULL "
                    "GROUP BY display_name ORDER BY COUNT(*) DESC",
                    (conversation_key,),
                ).fetchall()
        finally:
            conn.close()
    return rows


def get_messages_since(conversation_key: str, since_iso: str, direction: str = "in") -> list[tuple[str | None, str]]:
    """[신규] /오늘대화요약용 - 지정 시각 이후의 (display_name, text)를 오래된 순서로 반환."""
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT display_name, text FROM messages "
                "WHERE conversation_key = ? AND direction = ? AND ts >= ? ORDER BY id ASC",
                (conversation_key, direction, since_iso),
            ).fetchall()
        finally:
            conn.close()
    return rows
