"""
포링푸드(poring_food/) 매시 일지 - 예전 cron("매시 3분 python main.py")을 봇 안으로 옮겼다.

- 매시 PORING_FOOD_RUN_MINUTE분(KST)에 poring_food.main.main()을 실행한다. 포링푸드 코드는
  동기(requests, LLM 호출 여러 번)라서 봇 이벤트 루프를 막지 않게 스레드에서 돌린다.
- 디스코드 방송은 PORING_DISCORD_CHANNEL_ID가 있으면 봇 계정이 그 채널에 직접 올리고,
  없으면 예전 웹훅(PORING_DISCORD_WEBHOOK_URL)으로 보낸다(poring_food/notifier.py).
- 봇이 꺼져 있던 시간의 회차는 건너뛴다(밀린 회차를 몰아서 돌리지 않음).
- 관리자는 /포링푸드실행 으로 지금 바로 한 회차를 돌려볼 수 있다(이전 회차가 도는 중이면 거절).
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import parse_qs, urlparse
from datetime import time as dtime, timedelta, timezone

import discord
from discord import Interaction, app_commands
from discord.ext import commands, tasks

from config import settings
from core import side_accounts
from core.side_accounts import aesun
from core.admin_auth import is_admin
from poring_food import runtime

log = logging.getLogger("poring_food")

KST = timezone(timedelta(hours=9))
RUN_TIMES = [dtime(hour=h, minute=settings.PORING_FOOD_RUN_MINUTE, tzinfo=KST) for h in range(24)]
DISCORD_LIMIT = 2000
AGENT_WEBHOOK_NAME = "포링푸드 에이전트"


class PoringFood(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()
        self._webhook: discord.Webhook | None = None
        self._webhook_retry_at = 0.0
        # 에이전트 채널이 스레드면 웹훅은 부모 채널 것이고, 보낼 때마다 이 스레드를 지정해야 한다
        self._webhook_thread: discord.Object | None = None

    async def cog_load(self) -> None:
        sender = self._send_discord if settings.PORING_DISCORD_CHANNEL_ID else None
        runtime.bind(asyncio.get_running_loop(), sender)
        if settings.PORING_AGENT_CHANNEL_ID:
            runtime.bind_agent_sender(self._send_as_agent)
        # [1회성] 인물 이름 변경 전 실행 기록을 새 이름으로 (조회 도구가 첫 회차 전에도 새 이름을 보게)
        from poring_food import rename_migration
        await asyncio.to_thread(rename_migration.migrate)
        if settings.PORING_FOOD_ENABLED:
            self.hourly.start()
            log.info("포링푸드 매시 %02d분 실행 예약 (디스코드: %s, 카톡 방: %s)",
                     settings.PORING_FOOD_RUN_MINUTE,
                     settings.PORING_DISCORD_CHANNEL_ID or ("웹훅" if settings.PORING_DISCORD_WEBHOOK_URL else "없음"),
                     settings.PORING_KAKAO_ROOM_ID or "없음")
        else:
            log.info("PORING_FOOD_ENABLED=0 - 포링푸드 매시 실행을 끈다.")

    async def cog_unload(self) -> None:
        self.hourly.cancel()

    async def _send_discord(self, text: str) -> None:
        channel_id = settings.PORING_DISCORD_CHANNEL_ID
        # 포링푸드 일지/장면은 애순이 계정으로 (토큰이 없거나 권한이 없으면 아메하나 계정)
        if await aesun.send(channel_id, text):
            return
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        for i in range(0, len(text), DISCORD_LIMIT):
            await channel.send(text[i:i + DISCORD_LIMIT])

    async def _send_as_agent(self, account: str, name: str, text: str, avatar_url: str = "") -> None:
        """
        에이전트의 말을 에이전트 채널에 올린다.
        1) 봇 계정이 있는 에이전트(애순이/소라): 그 계정으로
        2) 봇 토큰 없는 에이전트: 채널 웹훅으로 이름/아바타만 바꿔서 (사람마다 다른 사람처럼 보인다)
        3) 둘 다 안 되면 아메하나가 이름을 붙여 대신
        """
        channel_id = settings.PORING_AGENT_CHANNEL_ID
        acc = side_accounts.get(account) if account else None
        if acc is not None and await acc.send(channel_id, text):
            return
        hook = await self._agent_webhook()
        if hook is not None:
            try:
                kwargs = {"thread": self._webhook_thread} if self._webhook_thread else {}
                await hook.send(text[:DISCORD_LIMIT], username=name[:80], avatar_url=avatar_url or None,
                                allowed_mentions=discord.AllowedMentions.none(), **kwargs)
                return
            except discord.HTTPException as exc:
                log.warning("에이전트 웹훅 전송 실패 (%s) - 아메하나가 대신 올림: %s", name, exc)
                self._webhook = None
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(f"**{name}**: {text}"[:DISCORD_LIMIT])

    async def _agent_webhook(self) -> discord.Webhook | None:
        """
        봇 토큰 없는 에이전트용 채널 웹훅. .env의 PORING_AGENT_WEBHOOK_URL이 있으면 그걸, 없으면 카제 봇 ->
        아메하나 순으로 채널의 기존 웹훅을 찾거나 만든다("웹후크 관리" 권한 필요). 실패하면 1시간 뒤 다시 시도.

        [수정] 에이전트 채널이 스레드일 때: 웹훅은 스레드가 아니라 부모 채널에 속해서, 보낼 때 thread를
        지정하지 않으면 부모 채널로 간다(애순이는 봇 계정이라 스레드에, 웹훅 인물은 부모 채널에 올라가던 문제).
        URL 끝의 ?thread_id=는 discord.py가 버리므로 직접 읽고, 없으면 에이전트 채널이 스레드인지 확인한다.
        """
        if self._webhook is not None:
            return self._webhook
        if settings.PORING_AGENT_WEBHOOK_URL:
            url = settings.PORING_AGENT_WEBHOOK_URL
            thread_id = parse_qs(urlparse(url).query).get("thread_id", [""])[0]
            self._webhook = discord.Webhook.from_url(url.split("?", 1)[0], client=self.bot)
            if thread_id.isdigit():
                self._webhook_thread = discord.Object(id=int(thread_id))
            else:
                await self._detect_agent_thread()
            return self._webhook
        if time.monotonic() < self._webhook_retry_at:
            return None
        kaje = side_accounts.get("kaje")
        for client in (kaje.client if kaje else None, self.bot):
            if client is None:
                continue
            try:
                ch_id = settings.PORING_AGENT_CHANNEL_ID
                channel = client.get_channel(ch_id) or await client.fetch_channel(ch_id)
                if isinstance(channel, discord.Thread):  # 스레드엔 웹훅을 못 만든다 - 부모 채널 웹훅 + 스레드 지정
                    self._webhook_thread = discord.Object(id=channel.id)
                    channel = channel.parent or await client.fetch_channel(channel.parent_id)
                hooks = await channel.webhooks()
                hook = next((h for h in hooks if h.name == AGENT_WEBHOOK_NAME and h.token), None)
                if hook is None:
                    hook = await channel.create_webhook(name=AGENT_WEBHOOK_NAME, reason="포링푸드 에이전트 대화")
                    log.info("포링푸드 에이전트 웹훅을 만들었다 (%s)", client.user)
                self._webhook = hook
                return hook
            except discord.HTTPException as exc:
                log.warning("에이전트 웹훅 준비 실패 (%s): %s", client.user, exc)
        self._webhook_retry_at = time.monotonic() + 3600
        log.warning("에이전트 웹훅을 못 만들었다 - 카제/아메하나에 채널 '웹후크 관리' 권한을 주거나 "
                    "PORING_AGENT_WEBHOOK_URL을 넣어라. 그때까지는 아메하나가 이름을 붙여 대신 올린다.")
        return None

    async def _detect_agent_thread(self) -> None:
        """에이전트 채널이 스레드면 웹훅을 보낼 때 지정할 스레드로 기억한다."""
        try:
            ch_id = settings.PORING_AGENT_CHANNEL_ID
            channel = self.bot.get_channel(ch_id) or await self.bot.fetch_channel(ch_id)
        except discord.HTTPException:
            return
        if isinstance(channel, discord.Thread):
            self._webhook_thread = discord.Object(id=channel.id)

    async def _run_once(self) -> bool:
        """한 회차 실행. 이미 도는 중이면 False."""
        if self._lock.locked():
            return False
        async with self._lock:
            from poring_food import main as poring_main
            started = time.monotonic()
            log.info("포링푸드 회차 시작")
            try:
                await asyncio.to_thread(poring_main.main)
            except Exception:
                log.exception("포링푸드 회차 실행 실패 - 다음 회차에 다시 시도")
            else:
                log.info("포링푸드 회차 완료 (%.1f초)", time.monotonic() - started)
        return True

    @tasks.loop(time=RUN_TIMES)
    async def hourly(self) -> None:
        if not await self._run_once():
            log.warning("이전 포링푸드 회차가 아직 실행 중이라 이번 회차는 건너뜀")

    @hourly.before_loop
    async def _before_hourly(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="포링푸드실행", description="[관리자] 포링푸드 일지 한 회차를 지금 바로 실행합니다")
    async def run_now(self, interaction: Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        if self._lock.locked():
            await interaction.response.send_message("⏳ 이미 한 회차가 실행 중이에요.", ephemeral=True)
            return
        await interaction.response.send_message("▶️ 포링푸드 회차를 실행할게요. 결과는 방송 채널/카톡방으로 나가요.", ephemeral=True)
        await self._run_once()


async def setup(bot: commands.Bot):
    await bot.add_cog(PoringFood(bot))
