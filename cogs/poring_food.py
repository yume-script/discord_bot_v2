"""
포링푸드(poring_food/) 매시 일지 - 예전 cron("매시 3분 python main.py")을 봇 안으로 옮겼다.

- 매시 PORING_FOOD_RUN_MINUTE분(KST)에 poring_food.main.main()을 실행한다. 포링푸드 코드는
  동기(requests, LLM 호출 여러 번)라서 봇 이벤트 루프를 막지 않게 스레드에서 돌린다.
- 디스코드 방송은 PORING_DISCORD_CHANNEL_ID가 있으면 봇 계정이 그 채널에 직접 올리고,
  없으면 예전 웹훅(PORING_DISCORD_WEBHOOK_URL)으로 보낸다(poring_food/notifier.py).
- 봇이 꺼져 있던 시간의 회차는 건너뛴다(밀린 회차를 몰아서 돌리지 않음).
- 관리자는 /포링푸드실행 으로 지금 바로 한 회차를 돌려볼 수 있다(이전 회차가 도는 중이면 거절).
- 관리자 개입 (세계 밖에서 조종 - 에이전트는 관리자가 한 줄 모르고 세상 일/누군가의 연락으로 받는다):
  · /포링푸드사건 대상 내용 - 세계 엔진에 지금 사건을 넣는다 (대상: 에이전트/회사/동네, 쉼표로 여럿)
  · /포링푸드메시지 대상 내용 [보낸사람] - 에이전트 받은편지함에 메시지(귓속말)를 넣는다
  둘 다 "바로반영"(기본 켬)이면 일지/방송 없이 에이전트 회차만 바로 돌아서 받은 사람이 지금 반응한다.
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
        # 채널별 (웹훅, 스레드) - 채널이 스레드면 웹훅은 부모 채널 것이고 보낼 때마다 스레드를 지정해야 한다
        self._webhooks: dict[int, tuple[discord.Webhook, discord.Object | None]] = {}
        self._webhook_retry_at: dict[int, float] = {}

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

    async def _send_as_agent(self, account: str, name: str, text: str, avatar_url: str = "", channel_id: int = 0) -> None:
        """
        에이전트의 말을 채널에 올린다 (channel_id 기본: 에이전트 대화 채널, 회사 단톡방 등은 지정).
        1) 봇 계정이 있는 에이전트(애순이/소라): 그 계정으로
        2) 봇 토큰 없는 에이전트: 그 채널의 웹훅으로 이름/아바타만 바꿔서 (사람마다 다른 사람처럼 보인다)
        3) 둘 다 안 되면 아메하나가 이름을 붙여 대신
        """
        channel_id = channel_id or settings.PORING_AGENT_CHANNEL_ID
        acc = side_accounts.get(account) if account else None
        if acc is not None and await acc.send(channel_id, text):
            return
        hook, thread = await self._agent_webhook(channel_id)
        if hook is not None:
            try:
                kwargs = {"thread": thread} if thread else {}
                await hook.send(text[:DISCORD_LIMIT], username=name[:80], avatar_url=avatar_url or None,
                                allowed_mentions=discord.AllowedMentions.none(), **kwargs)
                return
            except discord.HTTPException as exc:
                log.warning("에이전트 웹훅 전송 실패 (%s, 채널 %s) - 아메하나가 대신 올림: %s", name, channel_id, exc)
                self._webhooks.pop(channel_id, None)
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(f"**{name}**: {text}"[:DISCORD_LIMIT])

    def _webhook_url_for(self, channel_id: int) -> str:
        if channel_id == settings.PORING_AGENT_CHANNEL_ID:
            return settings.PORING_AGENT_WEBHOOK_URL
        if channel_id == settings.PORING_GROUP_CHAT_CHANNEL_ID:
            return settings.PORING_GROUP_WEBHOOK_URL
        return ""

    async def _agent_webhook(self, channel_id: int) -> tuple[discord.Webhook | None, discord.Object | None]:
        """
        봇 토큰 없는 에이전트용 채널 웹훅과 (스레드면) 지정할 스레드. 채널마다 따로 기억한다.
        .env에 그 채널의 웹훅 URL(PORING_AGENT_WEBHOOK_URL / PORING_GROUP_WEBHOOK_URL)이 있으면 그걸, 없으면
        카제 봇 -> 아메하나 순으로 채널의 기존 웹훅을 찾거나 만든다("웹후크 관리" 권한 필요). 실패하면 1시간 뒤 다시.

        [수정] 채널이 스레드일 때: 웹훅은 스레드가 아니라 부모 채널에 속해서, 보낼 때 thread를 지정하지 않으면
        부모 채널로 간다. URL 끝의 ?thread_id=는 discord.py가 버리므로 직접 읽고, 없으면 채널이 스레드인지 확인한다.
        """
        if channel_id in self._webhooks:
            return self._webhooks[channel_id]
        url = self._webhook_url_for(channel_id)
        if url:
            thread_id = parse_qs(urlparse(url).query).get("thread_id", [""])[0]
            hook = discord.Webhook.from_url(url.split("?", 1)[0], client=self.bot)
            thread = discord.Object(id=int(thread_id)) if thread_id.isdigit() else await self._thread_of(channel_id)
            self._webhooks[channel_id] = (hook, thread)
            return self._webhooks[channel_id]
        if time.monotonic() < self._webhook_retry_at.get(channel_id, 0.0):
            return None, None
        kaje = side_accounts.get("kaje")
        for client in (kaje.client if kaje else None, self.bot):
            if client is None:
                continue
            try:
                channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
                thread = None
                if isinstance(channel, discord.Thread):  # 스레드엔 웹훅을 못 만든다 - 부모 채널 웹훅 + 스레드 지정
                    thread = discord.Object(id=channel.id)
                    channel = channel.parent or await client.fetch_channel(channel.parent_id)
                hooks = await channel.webhooks()
                hook = next((h for h in hooks if h.name == AGENT_WEBHOOK_NAME and h.token), None)
                if hook is None:
                    hook = await channel.create_webhook(name=AGENT_WEBHOOK_NAME, reason="포링푸드 에이전트 대화")
                    log.info("포링푸드 에이전트 웹훅을 만들었다 (%s, 채널 %s)", client.user, channel.id)
                self._webhooks[channel_id] = (hook, thread)
                return hook, thread
            except discord.HTTPException as exc:
                log.warning("에이전트 웹훅 준비 실패 (%s, 채널 %s): %s", client.user, channel_id, exc)
        self._webhook_retry_at[channel_id] = time.monotonic() + 3600
        log.warning("채널 %s의 에이전트 웹훅을 못 만들었다 - 카제/아메하나에 '웹후크 관리' 권한을 주거나 웹훅 URL을 "
                    ".env에 넣어라. 그때까지는 아메하나가 이름을 붙여 대신 올린다.", channel_id)
        return None, None

    async def _thread_of(self, channel_id: int) -> discord.Object | None:
        """채널이 스레드면 웹훅을 보낼 때 지정할 스레드."""
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
        return discord.Object(id=channel.id) if isinstance(channel, discord.Thread) else None

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

    async def _agents_now(self) -> bool:
        """일지 없이 에이전트 회차만 (관리자 개입 바로 반영). 다른 회차가 도는 중이면 False - 다음 회차에 반영."""
        if self._lock.locked():
            return False
        async with self._lock:
            from poring_food import agents
            try:
                await asyncio.to_thread(agents.run_agents_only)
            except Exception:
                log.exception("관리자 개입 에이전트 회차 실패 - 다음 회차에 반영")
        return True

    async def _target_autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        from poring_food import agents
        names = await asyncio.to_thread(agents.target_names)
        last = current.split(",")[-1].strip()
        head = ",".join(p.strip() for p in current.split(",")[:-1] if p.strip())
        out = []
        for n in names:
            if last in n:
                value = f"{head},{n}" if head else n
                out.append(app_commands.Choice(name=value[:100], value=value[:100]))
        return out[:25]

    async def _agent_autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        from poring_food import agents
        names = await asyncio.to_thread(lambda: [a["name"] for a in agents.load_agents()])
        return [app_commands.Choice(name=n, value=n) for n in names if current in n][:25]

    @app_commands.command(name="포링푸드사건", description="[관리자] 세계 엔진에 지금 사건을 일으킵니다 (에이전트는 세상 일로 받음)")
    @app_commands.describe(대상="에이전트 이름/회사 이름/동네 (쉼표로 여럿)", 내용="실제로 일어난 일 1~2문장 (당사자가 알게 되는 내용)",
                           제목="짧은 사건 제목 (비우면 내용 앞부분)", 바로반영="에이전트 회차를 지금 돌려서 바로 반응하게 (기본 켬)")
    @app_commands.autocomplete(대상=_target_autocomplete)
    async def inject_event(self, interaction: Interaction, 대상: str, 내용: str, 제목: str = "", 바로반영: bool = True):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        from poring_food import agents
        targets = [t.strip() for t in 대상.split(",") if t.strip()]
        got = await asyncio.to_thread(agents.inject_event, targets, 내용, 제목)
        if not got:
            valid = await asyncio.to_thread(agents.target_names)
            await interaction.response.send_message(
                f"⚠️ 사건을 받을 에이전트가 없어요. 대상은 이 중에서: {', '.join(valid)}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🌩️ 사건 발생: {제목 or 내용[:40]} → {', '.join(got)}"
            + (" (지금 반응 중…)" if 바로반영 else " (다음 회차에 반응)"), ephemeral=True)
        if 바로반영 and not await self._agents_now():
            await interaction.followup.send("⏳ 다른 회차가 도는 중이라 다음 회차에 반응해요.", ephemeral=True)

    @app_commands.command(name="포링푸드메시지", description="[관리자] 에이전트에게 메시지(귓속말)를 보냅니다")
    @app_commands.describe(대상="받을 에이전트", 내용="메시지 내용", 보낸사람="에이전트에게 보이는 보낸 사람 (비우면 '누군가')",
                           바로반영="에이전트 회차를 지금 돌려서 바로 반응하게 (기본 켬)")
    @app_commands.autocomplete(대상=_agent_autocomplete)
    async def whisper(self, interaction: Interaction, 대상: str, 내용: str, 보낸사람: str = "", 바로반영: bool = True):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        from poring_food import agents
        if not await asyncio.to_thread(agents.send_message, 대상, 내용, 보낸사람):
            await interaction.response.send_message(f"⚠️ '{대상}'라는 에이전트가 없어요.", ephemeral=True)
            return
        asleep = await asyncio.to_thread(agents.is_asleep_now, 대상)
        await interaction.response.send_message(
            f"✉️ {대상}에게 전달: {내용}"
            + (" (지금 자는 중 - 깨어나면 읽어요)" if asleep else (" (지금 반응 중…)" if 바로반영 else " (다음 회차에 반응)")),
            ephemeral=True)
        if 바로반영 and not asleep and not await self._agents_now():
            await interaction.followup.send("⏳ 다른 회차가 도는 중이라 다음 회차에 반응해요.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PoringFood(bot))
