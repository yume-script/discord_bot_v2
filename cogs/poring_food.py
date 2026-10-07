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
from datetime import time as dtime, timedelta, timezone

from discord import Interaction, app_commands
from discord.ext import commands, tasks

from config import settings
from core.side_accounts import aesun
from core.admin_auth import is_admin
from poring_food import runtime

log = logging.getLogger("poring_food")

KST = timezone(timedelta(hours=9))
RUN_TIMES = [dtime(hour=h, minute=settings.PORING_FOOD_RUN_MINUTE, tzinfo=KST) for h in range(24)]
DISCORD_LIMIT = 2000


class PoringFood(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._lock = asyncio.Lock()

    async def cog_load(self) -> None:
        sender = self._send_discord if settings.PORING_DISCORD_CHANNEL_ID else None
        runtime.bind(asyncio.get_running_loop(), sender)
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
