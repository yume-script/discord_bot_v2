"""
포링푸드(poring_food/) 매시 일지 - 예전 cron("매시 3분 python main.py")을 봇 안으로 옮겼다.

- 매시 PORING_FOOD_RUN_MINUTE분(KST)에 poring_food.main.main()을 실행한다. 포링푸드 코드는
  동기(requests, LLM 호출 여러 번)라서 봇 이벤트 루프를 막지 않게 스레드에서 돌린다.
- 디스코드 방송은 PORING_DISCORD_CHANNEL_ID가 있으면 봇 계정이 그 채널에 직접 올리고,
  없으면 예전 웹훅(PORING_DISCORD_WEBHOOK_URL)으로 보낸다(poring_food/notifier.py).
- 봇이 꺼져 있던 시간의 회차는 건너뛴다(밀린 회차를 몰아서 돌리지 않음).
- 대화/단톡방/연재 장면은 회차가 만들어 둔 시각대로 예약함(poring_food/outbox.py)에서 몇 초마다 꺼내 차례대로 올린다
  (한 시간치 대화가 몇 분 사이에 몰려 올라오지 않게, 다른 곳 대화가 중간에 섞이게).
- 관리자는 /포링푸드실행 으로 지금 바로 한 회차를 돌려볼 수 있다(이전 회차가 도는 중이면 거절).
- 관리자 개입 (세계 밖에서 조종):
  (예전의 /포링푸드사건, /포링푸드메시지는 매시 회차와 겹치면 소식이 사라질 수 있어서 내렸다 -
   agents.inject_event/send_message 함수는 다음 아이디어를 위해 남겨 둔다)
  · /포링푸드모임 주제 [참가자] [장소] [발언수] - 여러 에이전트가 한자리에 모여 돌아가며 말하고 결론을 낸다
  · /포링푸드연대기 [날짜] - 그날 세계에서 있었던 일(하루 요약/원본 사건)과 진행 중인 일을 본다
  · /포링푸드이웃 [드라마] - 드라마 인물들을 지금 바로 동네로 이사 오게 한다 (원래는 주 1회 자동)
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
            self.drain_outbox.start()
            log.info("포링푸드 매시 %02d분 실행 예약 (디스코드: %s, 카톡 방: %s)",
                     settings.PORING_FOOD_RUN_MINUTE,
                     settings.PORING_DISCORD_CHANNEL_ID or ("웹훅" if settings.PORING_DISCORD_WEBHOOK_URL else "없음"),
                     settings.PORING_KAKAO_ROOM_ID or "없음")
        else:
            log.info("PORING_FOOD_ENABLED=0 - 포링푸드 매시 실행을 끈다.")

    async def cog_unload(self) -> None:
        self.hourly.cancel()
        self.drain_outbox.cancel()

    @tasks.loop(seconds=5)
    async def drain_outbox(self) -> None:
        """예약함에서 보낼 때가 된 글을 시각 순서대로 보낸다 (디스코드 rate limit 대비 글 사이 1초)."""
        from poring_food import notifier, outbox
        try:
            items = await asyncio.to_thread(outbox.take_due)
        except Exception:  # noqa: BLE001
            log.exception("포링푸드 예약함 읽기 실패")
            return
        for it in items:
            try:
                if it["kind"] == "agent":
                    await self._send_as_agent(it.get("account", ""), it["name"], it["text"], it.get("avatar", ""),
                                              it.get("channel_id", 0))
                elif it["kind"] == "discord":
                    if settings.PORING_DISCORD_CHANNEL_ID:
                        await self._send_discord(it["text"])
                    else:
                        await asyncio.to_thread(notifier.send_to_discord, it["text"])
                elif it["kind"] == "kakao":
                    await asyncio.to_thread(notifier.send_to_local_bot, it["text"])
                elif it["kind"] == "file":
                    await self._send_file_as_agent(it.get("name", "동네 소식"), it["path"], it.get("text", ""),
                                                   it.get("channel_id", 0))
            except Exception:  # noqa: BLE001 - 한 글이 실패해도 다음 글은 보낸다
                log.exception("포링푸드 예약 글 전송 실패 (%s)", it.get("kind"))
            await asyncio.sleep(1)

    @drain_outbox.before_loop
    async def _before_drain(self) -> None:
        await self.bot.wait_until_ready()

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

    async def _send_file_as_agent(self, name: str, path: str, caption: str = "", channel_id: int = 0) -> None:
        """그림 한 장을 에이전트 채널에 (웹훅이면 그 이름으로, 안 되면 아메하나가)."""
        import os
        if not os.path.exists(path):
            log.warning("포링푸드 그림 파일이 없음: %s", path)
            return
        channel_id = channel_id or settings.PORING_AGENT_CHANNEL_ID
        hook, thread = await self._agent_webhook(channel_id)
        if hook is not None:
            try:
                kwargs = {"thread": thread} if thread else {}
                await hook.send(caption[:DISCORD_LIMIT] or None, username=name[:80], file=discord.File(path),
                                allowed_mentions=discord.AllowedMentions.none(), **kwargs)
                return
            except discord.HTTPException as exc:
                log.warning("포링푸드 그림 웹훅 전송 실패 - 아메하나가 대신 올림: %s", exc)
        channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
        await channel.send(caption[:DISCORD_LIMIT] or None, file=discord.File(path))

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

    async def _run_once(self, wait: bool = False) -> bool:
        """한 회차 실행. 이미 도는 중이면 False (wait=True면 모임/관리자 개입이 끝날 때까지 기다렸다가 돈다)."""
        if self._lock.locked() and not wait:
            return False
        async with self._lock:
            from poring_food import main as poring_main
            from poring_food import notifier
            from poring_food.clock import now_kst
            started = time.monotonic()
            log.info("포링푸드 회차 시작")
            # 회차 시작/끝 알림은 카톡방에만 (디스코드엔 대화만)
            await asyncio.to_thread(notifier.tick_notice, f"🕒 {now_kst():%H:%M} 포링푸드 회차 시작 - "
                                                          "대화는 다 만들어지면 차례대로 올라가요")
            failed = False
            try:
                await asyncio.to_thread(poring_main.main)
            except Exception:
                failed = True
                log.exception("포링푸드 회차 실행 실패 - 다음 회차에 다시 시도")
            else:
                log.info("포링푸드 회차 완료 (%.1f초)", time.monotonic() - started)
            try:
                summary = await asyncio.to_thread(notifier.tick_summary, time.monotonic() - started, failed)
                await asyncio.to_thread(notifier.tick_notice, summary)
            except Exception:
                log.exception("포링푸드 회차 끝 알림 실패")
        return True

    @tasks.loop(time=RUN_TIMES)
    async def hourly(self) -> None:
        if self._lock.locked():
            log.info("모임/관리자 개입이 진행 중 - 끝나면 이번 회차를 이어서 실행")
        await self._run_once(wait=True)

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

    async def _agents_csv_autocomplete(self, interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
        from poring_food import agents
        names = await asyncio.to_thread(lambda: [a["name"] for a in agents.load_agents()])
        last = current.split(",")[-1].strip()
        head = ",".join(p.strip() for p in current.split(",")[:-1] if p.strip())
        out = []
        for n in names:
            if last in n and n not in head.split(","):
                value = f"{head},{n}" if head else n
                out.append(app_commands.Choice(name=value[:100], value=value[:100]))
        return out[:25]

    @app_commands.command(name="포링푸드모임", description="[관리자] 에이전트들이 모여 토론합니다 (결론 없으면 5차까지 자동 소집 후 투표)")
    @app_commands.describe(주제="모임 주제 (예: 우리 동네 이름 짓기)", 참가자="에이전트 이름 쉼표로 (비우면 깨어 있는 동네 단톡방 멤버)",
                           장소="모임 장소 (비우면 동네 주민센터 회의실)", 발언수="전체 발언 수 4~30 (기본 12)")
    @app_commands.autocomplete(참가자=_agents_csv_autocomplete)
    async def meeting(self, interaction: Interaction, 주제: str, 참가자: str = "", 장소: str = "",
                      발언수: app_commands.Range[int, 4, 30] = 12):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        if self._lock.locked():
            await interaction.response.send_message("⏳ 다른 회차가 도는 중이에요. 잠시 뒤에 다시 해 주세요.", ephemeral=True)
            return
        await interaction.response.send_message(f"🏛️ '{주제}' 모임을 엽니다. 대화 채널에서 진행돼요.", ephemeral=True)
        from poring_food import agents
        names = [n.strip() for n in 참가자.split(",") if n.strip()] or None
        async with self._lock:
            try:
                res = await asyncio.to_thread(agents.run_meeting, 주제, names, 장소, 발언수)
            except Exception:
                log.exception("모임 실행 실패")
                await interaction.followup.send("⚠️ 모임 진행 중 오류가 났어요. 로그를 확인해 주세요.", ephemeral=True)
                return
        if len(res["participants"]) < 2:
            msg = "⚠️ 모일 수 있는 사람이 2명보다 적어요." + (f" (자는 중: {', '.join(res['asleep'])})" if res["asleep"] else "")
        else:
            msg = (f"🏛️ {res['round']}차 모임 끝 ({res['lines']}마디) - {'✅ 결정' if res['decided'] else '⏸️ 미결'}: {res['conclusion']}"
                   + (f"\n📅 다음 모임: {res['next_at'][5:16].replace('T', ' ')} (자동 소집)" if res.get("next_at") else "")
                   + (f"\n자는 중이라 빠짐: {', '.join(res['asleep'])}" if res["asleep"] else ""))
        await interaction.followup.send(msg, ephemeral=True)

    @app_commands.command(name="포링푸드연대기", description="[관리자] 그날 가상 세계에서 있었던 일과 진행 중인 일을 봅니다")
    @app_commands.describe(날짜="2026-10-09, 10/09, 오늘, 어제 (비우면 어제)")
    async def chronicle_view(self, interaction: Interaction, 날짜: str = ""):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        from poring_food import chronicle
        date = chronicle.parse_date(날짜)
        if not date:
            await interaction.response.send_message("⚠️ 날짜 형식을 모르겠어요. 예: 2026-10-09, 10/09, 오늘, 어제", ephemeral=True)
            return
        text = await asyncio.to_thread(chronicle.lookup, date)
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="포링푸드이웃", description="[관리자] 드라마 인물들을 지금 동네로 이사 오게 합니다 (원래 주 1회 자동)")
    @app_commands.describe(드라마="드라마 제목 (비우면 후보 목록에서 아직 안 쓴 것)")
    async def newcomers_now(self, interaction: Interaction, 드라마: str = ""):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        if self._lock.locked():
            await interaction.response.send_message("⏳ 다른 회차가 도는 중이에요. 잠시 뒤에 다시 해 주세요.", ephemeral=True)
            return
        await interaction.response.send_message("🚚 새 이웃을 찾는 중이에요...", ephemeral=True)
        from poring_food import agents
        async with self._lock:
            try:
                res = await asyncio.to_thread(agents.move_in_newcomers, 드라마)
            except Exception:
                log.exception("새 이웃 이사 실패")
                await interaction.followup.send("⚠️ 이사 중 오류가 났어요. 로그를 확인해 주세요.", ephemeral=True)
                return
        if res["added"]:
            msg = f"🚚 「{res['drama']}」 {', '.join(r['name'] for r in res['added'])} 이사 완료" + (
                f" / 이사 감: {', '.join(res['moved_out'])}" if res.get("moved_out") else "")
        else:
            msg = f"⚠️ 이사 온 사람이 없어요 ({res.get('reason', '')})"
        await interaction.followup.send(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PoringFood(bot))
