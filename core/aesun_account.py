"""
애순이 디스코드 계정 - "말하기 전용" 두 번째 봇 계정.

봇 프로세스는 하나다. 메시지 수신/명령어/판단은 지금처럼 아메하나 계정(app.py의 AesunBot)이 전부
하고, 애순이 페르소나로 정해진 답만 이 계정으로 디스코드에 올린다:
- 카톡 연동 채널의 애순이 답장 (cogs/chat.py)
- 포링푸드 매시 일지 / 연재 드라마 장면 (cogs/poring_food.py)

그래서 이 클라이언트는 이벤트를 하나도 처리하지 않는다 - 같은 메시지에 두 봇이 각자 반응하는
중복 응답이 생길 수 없고, Message Content 같은 특수 권한(privileged intent)도 필요 없다.
AESUN_BOT_TOKEN이 없거나 로그인/전송에 실패하면 send()가 False를 돌려주고, 호출한 쪽은
예전처럼 아메하나 계정으로 보낸다.
"""
from __future__ import annotations

import asyncio
import logging

import discord

from config import settings

log = logging.getLogger("aesun_account")

DISCORD_LIMIT = 2000

_client: discord.Client | None = None
_task: asyncio.Task | None = None


async def start() -> None:
    """app.py가 봇을 띄울 때 한 번 호출한다. 토큰이 없으면 아무것도 안 한다."""
    global _client, _task
    token = settings.AESUN_BOT_TOKEN
    if not token:
        log.info("AESUN_BOT_TOKEN 없음 - 애순이 답장/포링푸드 방송도 아메하나 계정으로 보낸다.")
        return
    intents = discord.Intents.none()
    intents.guilds = True  # 채널/스레드 캐시용 (특수 권한 아님)
    allowed_mentions = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True)
    client = discord.Client(intents=intents, allowed_mentions=allowed_mentions)

    @client.event
    async def on_ready():
        log.info("애순이 계정 로그인: %s", client.user)

    async def runner() -> None:
        try:
            await client.start(token)
        except Exception:  # noqa: BLE001 - 애순이 계정 문제로 봇 전체가 죽으면 안 된다
            log.exception("애순이 계정 로그인 실패 - 아메하나 계정으로 대신 보낸다.")

    _client = client
    _task = asyncio.create_task(runner(), name="aesun-account")


async def close() -> None:
    if _client is not None and not _client.is_closed():
        await _client.close()


def is_ready() -> bool:
    return _client is not None and _client.is_ready()


def is_own_message(message: discord.Message) -> bool:
    """애순이 계정이 올린 메시지인지 - 아메하나가 이걸 새 대화로 착각해서 반응하면 안 된다."""
    return _client is not None and _client.user is not None and message.author.id == _client.user.id


async def send(channel_id: int, text: str, reply_to: int | None = None) -> bool:
    """
    애순이 계정으로 channel_id(채널/스레드)에 올린다. reply_to가 있으면 그 메시지에 답장으로 단다.
    한 조각도 못 보냈으면 False (호출한 쪽이 아메하나 계정으로 대신 보낸다).
    """
    if not is_ready():
        return False
    if not text or not text.strip():
        text = "(빈 응답)"
    chunks = [text[i:i + DISCORD_LIMIT] for i in range(0, len(text), DISCORD_LIMIT)]
    sent = 0
    try:
        channel = _client.get_channel(channel_id) or await _client.fetch_channel(channel_id)
        for chunk in chunks:
            reference = None
            if reply_to and sent == 0:
                reference = discord.MessageReference(
                    message_id=reply_to, channel_id=channel_id, fail_if_not_exists=False
                )
            await channel.send(chunk, reference=reference)
            sent += 1
    except discord.HTTPException as exc:
        # 403이면 애순이 봇이 그 채널/스레드에 초대되지 않았거나 보내기 권한이 없는 것
        log.warning("애순이 계정 전송 실패 (channel=%s, %d/%d 조각 전송): %s", channel_id, sent, len(chunks), exc)
    return sent > 0
