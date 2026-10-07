"""
아메하나 말고 같은 프로세스에서 같이 로그인하는 보조 디스코드 계정들 - 이벤트를 하나도 처리하지 않는다.

봇 프로세스는 하나다. 메시지 수신/명령어/판단은 아메하나 계정(app.py의 AesunBot)이 전부 하고,
보조 계정은 필요할 때 send()로 말만 한다. 그래서 같은 메시지에 여러 봇이 각자 반응하는 중복
응답이 생길 수 없고, Message Content 같은 특수 권한(privileged intent)이나 명령어 등록도 필요 없다.

- 애순이(AESUN_BOT_TOKEN): 카톡 연동 채널의 애순이 답장, 포링푸드 일지/연재 드라마 장면 (cogs/chat.py,
  cogs/poring_food.py)
- 소라(SORA_BOT_TOKEN): 지금은 맡은 일 없이 로그인만 해 둔다 (SORA_BOT_ACTIVITY로 상태 메시지만)

토큰이 없거나 로그인/전송에 실패하면 send()가 False를 돌려주고, 호출한 쪽은 아메하나 계정으로 보낸다.
"""
from __future__ import annotations

import asyncio
import logging

import discord

from config import settings

log = logging.getLogger("side_accounts")

DISCORD_LIMIT = 2000


class SideAccount:
    def __init__(self, name: str, token: str, activity: str = ""):
        self.name = name
        self._token = token
        self._activity = activity
        self._client: discord.Client | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """app.py가 봇을 띄울 때 한 번 호출한다. 토큰이 없으면 아무것도 안 한다."""
        if not self._token:
            log.info("%s 계정 토큰 없음 - 로그인하지 않는다.", self.name)
            return
        intents = discord.Intents.none()
        intents.guilds = True  # 채널/스레드 캐시용 (특수 권한 아님)
        allowed_mentions = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True)
        activity = discord.CustomActivity(name=self._activity) if self._activity else None
        client = discord.Client(intents=intents, allowed_mentions=allowed_mentions, activity=activity)

        @client.event
        async def on_ready():
            log.info("%s 계정 로그인: %s", self.name, client.user)

        async def runner() -> None:
            try:
                await client.start(self._token)
            except Exception:  # noqa: BLE001 - 보조 계정 문제로 봇 전체가 죽으면 안 된다
                log.exception("%s 계정 로그인 실패 - 이 계정 없이 계속한다.", self.name)

        self._client = client
        self._task = asyncio.create_task(runner(), name=f"side-account-{self.name}")

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed():
            await self._client.close()

    def is_ready(self) -> bool:
        return self._client is not None and self._client.is_ready()

    def is_own_message(self, message: discord.Message) -> bool:
        client = self._client
        return client is not None and client.user is not None and message.author.id == client.user.id

    async def send(self, channel_id: int, text: str, reply_to: int | None = None) -> bool:
        """
        이 계정으로 channel_id(채널/스레드)에 올린다. reply_to가 있으면 그 메시지에 답장으로 단다.
        한 조각도 못 보냈으면 False (호출한 쪽이 아메하나 계정으로 대신 보낸다).
        """
        if not self.is_ready():
            return False
        if not text or not text.strip():
            text = "(빈 응답)"
        chunks = [text[i:i + DISCORD_LIMIT] for i in range(0, len(text), DISCORD_LIMIT)]
        sent = 0
        try:
            channel = self._client.get_channel(channel_id) or await self._client.fetch_channel(channel_id)
            for chunk in chunks:
                reference = None
                if reply_to and sent == 0:
                    reference = discord.MessageReference(
                        message_id=reply_to, channel_id=channel_id, fail_if_not_exists=False
                    )
                await channel.send(chunk, reference=reference)
                sent += 1
        except discord.HTTPException as exc:
            # 403이면 이 봇이 그 채널/스레드에 초대되지 않았거나 보내기 권한이 없는 것
            log.warning("%s 계정 전송 실패 (channel=%s, %d/%d 조각 전송): %s",
                        self.name, channel_id, sent, len(chunks), exc)
        return sent > 0


aesun = SideAccount("애순이", settings.AESUN_BOT_TOKEN)
sora = SideAccount("소라", settings.SORA_BOT_TOKEN, activity=settings.SORA_BOT_ACTIVITY)
ALL = (aesun, sora)


async def start_all() -> None:
    for account in ALL:
        await account.start()


async def close_all() -> None:
    for account in ALL:
        await account.close()


def is_own_message(message: discord.Message) -> bool:
    """보조 계정이 올린 메시지인지 - 아메하나가 이걸 새 대화로 착각해서 반응하면 안 된다."""
    return any(account.is_own_message(message) for account in ALL)
