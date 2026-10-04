"""
디스코드의 특정 채널(예: 토큰/자격정보 등을 적어둔 메모용 채널)을 온디맨드로 읽어서
답하는 도구.

SQLite 대화로그 도구와의 차이: SQLite는 "우리가 로깅을 시작한 이후"의 메시지만 있지만,
이 도구는 Discord API로 그 채널의 실제 히스토리를 그때그때 직접 읽어오므로 로깅 시작
전에 이미 있던 오래된 메시지도 그대로 볼 수 있다.

AesunBot 인스턴스를 app.py의 setup_hook에서 set_bot_client()로 한 번 등록해두면,
이후 도구가 호출될 때마다 그 인스턴스로 실제 채널에 접근한다 - 매번 새로 봇을 띄우지
않는다(같은 프로세스 안의 이미 로그인된 봇을 재사용).

[변경] 처음엔 channel.history()만 있으면 되는 줄 알았는데, "개인창고" 채널이 포럼
채널이라 실패했다(ForumChannel엔 history()가 없음 - 게시글=스레드 단위로 구성되는
구조라서). 포럼 채널(스레드들을 순회해서 각 스레드의 메시지를 모음)과 카테고리
채널(메시지가 없는 그룹핑 전용이라 하위 채널 목록만 알려줌)도 같이 처리하도록 보강함.
"""
from __future__ import annotations

import logging

import discord
from langchain_core.tools import tool

log = logging.getLogger("discord_reader")

_bot = None


def set_bot_client(bot) -> None:
    global _bot
    _bot = bot


def _msg_to_line(msg) -> str | None:
    text = msg.content or ""
    if not text and msg.embeds:
        for embed in msg.embeds:
            if embed.title:
                text += embed.title + " "
            if embed.description:
                text += embed.description
    text = text.strip()
    if not text:
        return None
    return f"[{msg.created_at.strftime('%Y-%m-%d %H:%M')}] {msg.author.display_name}: {text}"


async def _collect_from_messageable(channel, limit: int, keyword: str) -> list[str]:
    """TextChannel/VoiceChannel/Thread처럼 history()가 있는 채널에서 메시지를 모은다."""
    lines: list[str] = []
    async for msg in channel.history(limit=limit):
        line = _msg_to_line(msg)
        if line is None:
            continue
        if keyword and keyword.lower() not in line.lower():
            continue
        lines.append(line)
    return lines


async def _collect_from_forum(channel, limit: int, keyword: str) -> list[str]:
    """포럼 채널은 history()가 없고 게시글(스레드) 단위라, 활성+보관된 스레드를 모두
    순회하면서 각 스레드 안의 메시지를 모은다."""
    lines: list[str] = []
    threads = list(channel.threads)  # 활성 스레드(캐시됨)
    try:
        async for t in channel.archived_threads(limit=limit):
            threads.append(t)
    except Exception:
        pass  # 보관된 스레드 조회 권한이 없어도 활성 스레드만으로 계속 진행

    for thread in threads:
        lines.append(f"--- 게시글: {thread.name} ---")
        thread_lines = await _collect_from_messageable(thread, limit=limit, keyword=keyword)
        lines.extend(thread_lines)
    return lines


@tool
async def read_discord_channel(channel_id: str, keyword: str = "", limit: int = 30) -> str:
    """디스코드의 특정 채널(channel_id)의 최근 메시지를 실시간으로 읽어온다. 토큰/자격정보/
    공지처럼 특정 채널에 적어둔 내용을 찾아 답할 때 쓴다. keyword를 주면 그 문자열이 포함된
    메시지만 걸러서 반환(대소문자 무시) - 비우면 최근 limit개를 그대로 반환. limit 기본 30,
    최대 100. 포럼 채널(게시글=스레드 구조)도 자동으로 지원한다."""
    if _bot is None:
        return "디스코드 클라이언트가 아직 준비되지 않았어요."

    try:
        cid = int(channel_id)
    except (TypeError, ValueError):
        return f"channel_id가 올바른 숫자가 아니에요: {channel_id!r}"

    channel = _bot.get_channel(cid)
    if channel is None:
        try:
            channel = await _bot.fetch_channel(cid)
        except Exception as exc:
            return f"채널을 찾을 수 없어요 (channel_id={channel_id}): {exc}"

    if isinstance(channel, discord.CategoryChannel):
        child_names = ", ".join(c.name for c in channel.channels) or "(하위 채널 없음)"
        return f"'{channel.name}'은(는) 카테고리라 메시지가 없어요. 하위 채널: {child_names}"

    limit = max(1, min(int(limit or 30), 100))
    try:
        if isinstance(channel, discord.ForumChannel):
            lines = await _collect_from_forum(channel, limit=limit, keyword=keyword)
        else:
            lines = await _collect_from_messageable(channel, limit=limit, keyword=keyword)
    except Exception as exc:
        log.exception("채널 히스토리 조회 실패 (channel_id=%s)", channel_id)
        return f"채널을 읽는 중 오류가 발생했어요: {exc}"

    if not lines:
        return "해당 조건에 맞는 메시지를 찾지 못했어요."

    if not isinstance(channel, discord.ForumChannel):
        lines.reverse()  # 최신순으로 오므로 보기 좋게 오래된 것부터 나오도록 뒤집는다
    return "\n".join(lines)


DISCORD_READER_TOOLS = [read_discord_channel]

