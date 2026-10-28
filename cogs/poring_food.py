"""
포링푸드(poring_food/) 매시 일지 - 예전 cron("매시 3분 python main.py")을 봇 안으로 옮겼다.

- 매시 PORING_FOOD_RUN_MINUTE분(KST)에 poring_food.main.main()을 실행한다. 포링푸드 코드는
  동기(requests, LLM 호출 여러 번)라서 봇 이벤트 루프를 막지 않게 스레드에서 돌린다.
- 디스코드 방송은 PORING_DISCORD_CHANNEL_ID가 있으면 봇 계정이 그 채널에 직접 올리고,
  없으면 예전 웹훅(PORING_DISCORD_WEBHOOK_URL)으로 보낸다(poring_food/notifier.py).
- 봇이 꺼져 있던 시간의 회차는 건너뛴다(밀린 회차를 몰아서 돌리지 않음).
- 에이전트 대화는 준실시간(poring_food/live.py): 회차는 누가 언제 누구와 얘기할지만 정하고, 대화는 그 시각에 20초마다
  돌아가는 live_runner가 만들어 바로 올린다 (그 순간의 시각/상태로 - 한 시간 전 기준이라 문맥/시각이 어긋나던 문제).
- 통신방/연재 장면은 회차가 만들어 둔 시각대로 예약함(poring_food/outbox.py)에서 몇 초마다 꺼내 차례대로 올린다
  (한 시간치 대화가 몇 분 사이에 몰려 올라오지 않게, 다른 곳 대화가 중간에 섞이게).
- 관리자는 /포링푸드실행 으로 지금 바로 한 회차를 돌려볼 수 있다(이전 회차가 도는 중이면 거절).
- 관리자 개입 (세계 밖에서 조종):
  (예전의 /포링푸드사건, /포링푸드메시지는 매시 회차와 겹치면 소식이 사라질 수 있어서 내렸다 -
   agents.inject_event/send_message 함수는 다음 아이디어를 위해 남겨 둔다)
  · /포링푸드모임 주제 [참가자] [장소] [발언수] - 여러 에이전트가 한자리에 모여 돌아가며 말하고 결론을 낸다
  · /포링푸드연대기 [날짜] - 그날 세계에서 있었던 일(하루 요약/원본 사건)과 진행 중인 일을 본다
  · /포링푸드명단 [소속] - 지금 스스로 사는 인물들의 소속/자리/맡은 일/고정 여부/지금 하는 일, 최근 교체
  · /포링푸드침입 [지금] - 몬스터 침입 예보(위험도)와 최근 밤들, 지금=True면 바로 침입 (시험용)
  · /시청현황 - 지금 재생 중인 수 (Plex 종류별 + YM_PLAYER 텔레그램/IPTV, 숫자만 - 제목·계정 없음)
  · /포링푸드골목 - 마을 방어 배치 판 그림 + 방어 기금/놓인 것과 상태/손봐야 할 것/최근 기록
  · /포링푸드이웃 [작품] - 애니메이션 인물들을 지금 바로 마을로 이사 오게 한다 (원래는 주 1회 자동)
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
        self._image_tasks: set[asyncio.Task] = set()  # 인물의 /그림, /개미소리 만드는 중 (가비지 컬렉션 방지)
        self._web_runner = None          # Tautulli 웹훅 수신 (aiohttp)
        self._plex_last_poll = 0.0
        self._book_last_poll = 0.0

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
            self.live_runner.start()
            self.plex_watch.start()
            self.book_watch.start()
            self.load_guard.start()
            self.waitfree_pilot.start()
            await self._start_webhook()
            log.info("포링푸드 매시 %02d분 실행 예약 (디스코드: %s, 카톡 방: %s)",
                     settings.PORING_FOOD_RUN_MINUTE,
                     settings.PORING_DISCORD_CHANNEL_ID or ("웹훅" if settings.PORING_DISCORD_WEBHOOK_URL else "없음"),
                     settings.PORING_KAKAO_ROOM_ID or "없음")
        else:
            log.info("PORING_FOOD_ENABLED=0 - 포링푸드 매시 실행을 끈다.")

    async def cog_unload(self) -> None:
        self.hourly.cancel()
        self.drain_outbox.cancel()
        self.live_runner.cancel()
        self.plex_watch.cancel()
        self.book_watch.cancel()
        self.load_guard.cancel()
        self.waitfree_pilot.cancel()
        if self._web_runner:
            await self._web_runner.cleanup()

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
                    await self._send_file_as_agent(it.get("name", "마을 소식"), it["path"], it.get("text", ""),
                                                   it.get("channel_id", 0))
                elif it["kind"] in ("draw", "ant"):  # 인물의 /그림, /개미소리 - 만드는 데 시간이 걸려서 따로 돌린다
                    task = asyncio.create_task(self._make_and_send_image(it))
                    self._image_tasks.add(task)
                    task.add_done_callback(self._image_tasks.discard)
            except Exception:  # noqa: BLE001 - 한 글이 실패해도 다음 글은 보낸다
                log.exception("포링푸드 예약 글 전송 실패 (%s)", it.get("kind"))
            await asyncio.sleep(1)

    @tasks.loop(seconds=20)
    async def live_runner(self) -> None:
        """준실시간 대화(poring_food/live.py): 시각이 된 대화 하나를 지금 만들어 바로 올린다.
        회차/모임과 같은 잠금 - 회차가 도는 중이면 이번엔 건너뛰고, 대화 하나가 도는 동안엔 회차가 기다린다."""
        if self._lock.locked():
            return
        from poring_food import live
        async with self._lock:
            try:
                await asyncio.to_thread(live.run_one)
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 실시간 대화 실패")

    @tasks.loop(seconds=60)
    async def plex_watch(self) -> None:
        """Plex 상영 알림(poring_food/plex_watch.py): 새로 재생이 시작되면 마을 통신방에 알린다.
        재생 확인은 매분 하고, 새 재생이 있을 때만 회차/실시간 대화와 같은 잠금을 기다렸다가 알린다
        (예전엔 잠금이 잡혀 있으면 건너뛰었는데, 실시간 대화가 거의 늘 잠금을 잡고 있어서 한 번도 못 돌았다)."""
        from poring_food import plex_watch
        if not plex_watch.ENABLED:
            return
        # 웹훅을 받는 중이면 1분 확인은 가끔만 (웹훅이 빠뜨린 것 줍기) - Tautulli가 꺼져 Plex에 바로 묻는 중이면 웹훅도
        # 안 오니 매분
        if (plex_watch.webhook_enabled() and not plex_watch.using_fallback()
                and time.monotonic() - self._plex_last_poll < plex_watch.FALLBACK_POLL_SEC):
            return
        self._plex_last_poll = time.monotonic()
        sessions = await asyncio.to_thread(plex_watch.fetch)
        if not sessions or not await asyncio.to_thread(plex_watch.has_new, sessions):
            return
        async with self._lock:
            try:
                await asyncio.to_thread(plex_watch.process, sessions)
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 상영 알림 실패")

    @tasks.loop(seconds=60)
    async def load_guard(self) -> None:
        """결계 조절(poring_food/loadshed.py) + 감시탑 바로 확인(facilities.quick), 매분.
        재기/내리기/늦추기는 잠금 없이(자기 파일만 쓴다). 한 일이나 바뀐 컨테이너가 있으면 채널엔 바로 알리고,
        인물 세계(마을 사건 + 관련 인물의 즉시 반응)는 회차와 같은 잠금 안에서 바로 이어서 반영한다."""
        from poring_food import loadshed
        if not loadshed.ENABLED:
            return
        try:
            done = await asyncio.to_thread(loadshed.step)
        except Exception:  # noqa: BLE001
            log.exception("포링푸드 결계 조절 실패")
            return
        # 사람이 직접 끄고 켠 컨테이너도 방금 잰 목록으로 바로 잡는다 (감시탑 - 원래는 매시 회차에만 봤다)
        from poring_food import facilities
        try:
            changed = await asyncio.to_thread(lambda: facilities.quick(loadshed.running_by_server()))
        except Exception:  # noqa: BLE001
            log.exception("포링푸드 감시탑 바로 확인 실패")
            changed = []
        if not done and not changed:
            return
        # 1) 채널엔 바로 (잠금 없이 - 회차가 도는 중이어도 기다리지 않는다)
        try:
            if done:
                await asyncio.to_thread(loadshed.notify, done)
            if changed:
                await asyncio.to_thread(facilities.notify, changed)
        except Exception:  # noqa: BLE001
            log.exception("포링푸드 결계 조절/감시탑 알림 실패")
        # 2) 인물 세계엔 잠금 안에서 바로 이어서 (마을 사건 + 관련 인물이 다음 회차를 기다리지 않고 지금 반응)
        async with self._lock:
            try:
                if done:
                    await asyncio.to_thread(loadshed.reflect, done)
                if changed:
                    await asyncio.to_thread(facilities.react, changed)
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 결계 조절/감시탑 세계 반영 실패")

    @tasks.loop(minutes=30)
    async def waitfree_pilot(self) -> None:
        """짐꾼 담당 인물이 하루 한 번 기다무 충전 시간대를 스스로 살펴 바꾼다 (poring_food/waitfree.autopilot).
        원격 명령이 몇 분 걸릴 수 있어 결계 조절(매분)과 따로 돈다."""
        from poring_food import waitfree
        try:
            await asyncio.to_thread(waitfree.autopilot)
        except Exception:  # noqa: BLE001
            log.exception("포링푸드 기다무 시간대 자율 판단 실패")

    @waitfree_pilot.before_loop
    async def _before_waitfree(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """📣│알림방의 메일 관리 Apps Script 알림 -> 성벽 잔해 치우기/전령 우편함 (poring_food/street_mail.py). 웹훅 메시지만 본다."""
        from poring_food import street_mail
        if (not settings.PORING_FOOD_ENABLED or not street_mail.ENABLED or not message.webhook_id
                or message.channel.id != street_mail.CHANNEL_ID):
            return
        ev = street_mail.parse(message.author.name, message.content)
        if ev:
            asyncio.create_task(self._street_mail(ev))

    async def _street_mail(self, ev: dict) -> None:
        from poring_food import street_mail
        async with self._lock:  # 회차/실시간 대화가 도는 중이면 끝날 때까지 기다렸다가 (상태 파일이 겹치지 않게)
            try:
                await asyncio.to_thread(street_mail.handle, ev)
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 성벽 청소 소식 처리 실패 (%s)", ev.get("kind"))

    async def _start_webhook(self) -> None:
        """Tautulli 웹훅 수신 (PORING_WEBHOOK_PORT + PORING_WEBHOOK_TOKEN이 있을 때만).
        POST /plex, 헤더 X-Poring-Token - 재생 시작 순간 상영 알림 (poring_food/plex_watch.py)."""
        from poring_food import plex_watch
        if not plex_watch.webhook_enabled():
            return
        from aiohttp import web

        async def plex_hook(request: web.Request) -> web.Response:
            if request.headers.get("X-Poring-Token", "") != plex_watch.WEBHOOK_TOKEN:
                return web.Response(status=403, text="forbidden")
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return web.Response(status=400, text="bad json")
            session = plex_watch.parse_tautulli(body)
            if session:
                asyncio.create_task(self._plex_from_hook(session))
            return web.Response(text="ok")

        app = web.Application()
        app.router.add_post("/plex", plex_hook)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, "0.0.0.0", plex_watch.WEBHOOK_PORT).start()
        except OSError:
            log.exception("포링푸드 웹훅 포트 %s를 못 열었음 - 1분 확인으로만 돈다", plex_watch.WEBHOOK_PORT)
            await runner.cleanup()
            return
        self._web_runner = runner
        log.info("포링푸드 웹훅 수신 시작 (포트 %s, POST /plex)", plex_watch.WEBHOOK_PORT)

    async def _plex_from_hook(self, session: dict) -> None:
        from poring_food import plex_watch
        async with self._lock:  # 회차/실시간 대화가 도는 중이면 끝날 때까지 기다렸다가 (본 것 기록이 겹치지 않게)
            try:
                await asyncio.to_thread(plex_watch.process, [session])
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 상영 알림(웹훅) 실패")

    @tasks.loop(seconds=60)
    async def book_watch(self) -> None:
        """도서관 손님 알림(poring_food/book_watch.py): 북오아시스에서 누가 책을 읽기 시작하면 통신방에 알린다.
        북오아시스엔 웹훅이 없어서 PORING_BOOK_WATCH_SEC(기본 10분)마다 묻는다 (MCP가 매번 SSH라 자주는 무겁다)."""
        from poring_food import book_watch
        if not book_watch.ENABLED or time.monotonic() - self._book_last_poll < book_watch.POLL_SEC:
            return
        self._book_last_poll = time.monotonic()
        try:
            rows = await asyncio.wait_for(book_watch.fetch_async(), timeout=90)
        except Exception as e:  # noqa: BLE001 - 북오아시스가 잠깐 안 받아도 다음 차례에 다시
            log.warning("포링푸드 도서관 손님 확인 실패: %r", e)
            return
        if rows is None or not await asyncio.to_thread(book_watch.has_new, rows):
            return
        async with self._lock:
            try:
                await asyncio.to_thread(book_watch.process, rows)
            except Exception:  # noqa: BLE001
                log.exception("포링푸드 도서관 손님 알림 실패")

    @book_watch.before_loop
    async def _before_book(self) -> None:
        await self.bot.wait_until_ready()

    @plex_watch.before_loop
    async def _before_plex(self) -> None:
        await self.bot.wait_until_ready()

    @live_runner.before_loop
    async def _before_live(self) -> None:
        await self.bot.wait_until_ready()

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
        에이전트의 말을 채널에 올린다 (channel_id 기본: 에이전트 대화 채널, 회사 통신방 등은 지정).
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

    async def _make_and_send_image(self, it: dict) -> None:
        """포링푸드 인물의 /그림(봇 이미지 생성 큐) 또는 /개미소리(말풍선 짤)를 만들어 그 인물 이름으로 올린다."""
        import os
        from poring_food.config import STATE_DIR
        name = it.get("name", "마을 사람")
        try:
            if it["kind"] == "draw":
                from ai.image_engine import generate_image
                from core.concurrency import image_gen_pool
                from core.user_ref import UserRef

                async def job():
                    return await generate_image(it["prompt"])

                result = await image_gen_pool.submit(UserRef.from_discord(0, f"포링푸드 {name}"), job)
                data, ext = result.image_bytes, "png"
            else:
                from core import ant_voice
                data, ext = await ant_voice.generate_ant_voice(it["text"]), "webp"
            if not data:
                log.warning("포링푸드 %s 이미지를 못 만듦 (%s)", it["kind"], name)
                return
            folder = os.path.join(STATE_DIR, "images")
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, f"{it['kind']}_{int(time.time())}_{abs(hash(name)) % 10000}.{ext}")
            with open(path, "wb") as f:
                f.write(data)
            await self._send_file_as_agent(name, path, it.get("caption") or it.get("text", ""), it.get("channel_id", 0))
            log.info("포링푸드 %s 올림: %s", "그림" if it["kind"] == "draw" else "개미소리", name)
        except Exception:  # noqa: BLE001 - 그림 하나 실패해도 봇은 계속
            log.exception("포링푸드 %s 실패 (%s)", it["kind"], name)

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
            from poring_food import health, notifier
            from poring_food.clock import now_kst
            started = time.monotonic()
            log.info("포링푸드 회차 시작")
            # 회차 시작/끝 알림은 카톡방에만 (디스코드엔 대화만)
            await asyncio.to_thread(notifier.tick_notice, f"🕒 {now_kst():%H:%M} 포링푸드 회차 시작 - "
                                                          "대화는 다 만들어지면 차례대로 올라가요")
            failed = False
            await asyncio.to_thread(health.begin)
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
            try:  # 문제가 이어지면 카톡으로 원인을 짚어서 알린다 (poring_food/health.py)
                await asyncio.to_thread(health.check, time.monotonic() - started, failed)
            except Exception:
                log.exception("포링푸드 이상 감지 실패")
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
    @app_commands.describe(주제="모임 주제 (예: 우리 마을 이름 짓기)", 참가자="에이전트 이름 쉼표로 (비우면 깨어 있는 마을 통신방 멤버)",
                           장소="모임 장소 (비우면 촌장 회관 회의실)", 발언수="전체 발언 수 4~30 (기본 12)")
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

    @app_commands.command(name="포링푸드골목", description="[관리자] 마을 방어 배치 판 - 판 그림(지금 누가 어디 있는지 포함), 방어 기금, 놓인 것과 상태, 최근 기록")
    async def decor_view(self, interaction: Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from poring_food import decor
        text = await asyncio.to_thread(decor.summary)
        try:
            from poring_food import agents, characters
            people = await asyncio.to_thread(lambda: decor.people_from_states(characters.load_states(), agents.names()))
            path = await asyncio.to_thread(decor.render, None, people)
        except Exception:  # noqa: BLE001 - 그림을 못 그려도 글은 보여 준다
            log.exception("마을 방어 배치 판 그리기 실패")
            path = None
        if path:
            await interaction.followup.send(text, file=discord.File(path), ephemeral=True)
        else:
            await interaction.followup.send(text + "\n-# (판 그림은 글꼴/Pillow가 없어서 못 그렸어요)", ephemeral=True)

    @app_commands.command(name="포링푸드침입", description="[관리자] 몬스터 침입 - 오늘 밤 숲의 기척(위험도)과 최근 밤들 (지금 침입시키기도 가능)")
    @app_commands.describe(지금="True면 위험도와 상관없이 지금 바로 몬스터가 쳐들어온다 (시험용)")
    async def raid_view(self, interaction: Interaction, 지금: bool = False):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from poring_food import raids
        if 지금:
            async with self._lock:  # 회차/실시간 대화와 상태 파일이 겹치지 않게
                try:
                    await asyncio.to_thread(raids.run, None, True)
                except Exception:  # noqa: BLE001
                    log.exception("포링푸드 침입 실행 실패")
        from poring_food import loadshed
        text = await asyncio.to_thread(lambda: raids.summary() + "\n\n" + loadshed.summary())
        await interaction.followup.send(text[:1990], ephemeral=True)

    @app_commands.command(name="시청현황", description="[관리자] 지금 몇 개가 재생 중인지 (Plex + YM_PLAYER 텔레그램/IPTV, 숫자만)")
    async def watching_view(self, interaction: Interaction):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        from poring_food import loadshed
        text = await asyncio.to_thread(loadshed.watching_text)
        await interaction.followup.send(text[:1990], ephemeral=True)

    @app_commands.command(name="포링푸드명단", description="[관리자] 지금 스스로 사는 인물들의 소속/자리/맡은 일/지금 하는 일을 봅니다")
    @app_commands.describe(소속="도서관 / 극장 / 마을 (비우면 전부)")
    async def staff_view(self, interaction: Interaction, 소속: str = ""):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        from poring_food import agents
        pages = await asyncio.to_thread(agents.staff_overview, 소속.strip())
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @app_commands.command(name="포링푸드이웃", description="[관리자] 애니메이션 인물들을 지금 마을로 이사 오게 합니다 (원래 주 1회 자동)")
    @app_commands.describe(작품="애니메이션 제목 (비우면 후보 목록에서 아직 안 쓴 것)")
    async def newcomers_now(self, interaction: Interaction, 작품: str = ""):
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
                res = await asyncio.to_thread(agents.move_in_newcomers, 작품)
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
