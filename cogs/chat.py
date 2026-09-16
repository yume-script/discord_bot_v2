"""
디스코드 메시지 처리. 카톡 메시지도 브릿지가 "{발신자명}//{방ID}//{유저ID}" 닉네임으로
이 채널들(KATALK_LINKED_CHANNEL_IDS)에 올려주는 방식이라 같은 on_message로 함께 처리한다
(기존 app.py의 on_message 파이프라인을 그대로 이식).

카톡/디스코드가 core.autonomous_reply의 전역 상태(쿨다운/활성채널)를 공유하는 것도 기존과 동일.
"""
from __future__ import annotations

import logging

import discord
from discord import Message
from discord.ext import commands

from ai.rag_engine import a_query
from config import settings
from core import autonomous_reply
from core.kakao_relay import parse_kakao_author
from core.katalk_bridge import log_message, send_message
from core.user_ref import UserRef

log = logging.getLogger("chat")

GREETING_REPLY = "네! 저 여기 있어요. 궁금한 거 있으면 편하게 물어봐 주세요 :)"
FAILURE_REPLY = "어라, 지금 대답을 못 만들었어요. 잠시 후 다시 불러주세요."


class Chat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        # 기존 봇과 동일하게 "봇 자기 자신"만 거른다. 카톡 릴레이 메시지는 브릿지 계정(봇 또는
        # 웹훅)이 올리는 경우가 많아서, author.bot 전체를 걸러내면 카톡 메시지가 통째로 씹힌다.
        if message.author == self.bot.user:
            return

        content = message.content
        is_linked_channel = str(message.channel.id) in settings.KATALK_LINKED_CHANNEL_IDS

        kakao_user = parse_kakao_author(message.author.name) if is_linked_channel else None
        is_kakao = kakao_user is not None
        user = kakao_user or UserRef.from_discord(message.author.id, message.author.display_name)

        key = f"discord:{message.channel.id}"

        is_command = content.startswith("/")
        is_feed_message = content.startswith('{"feedType"')
        # TODO: 카톡 입장/퇴장 피드 메시지 처리 (기존 app_kakao_handler.handle_kakao_features 참고)
        # TODO: 카톡 닉네임 변경 알림 / 채팅 머니 지급 (money_system, check_and_update_nickname 이식 여부 결정 필요)

        # 대화 로그는 스킵 판단과 무관하게 항상 먼저 남긴다 (기존 봇이 이 순서를 [1-1]로 옮긴 이유와 동일 -
        # 늦게 기록하면 "봇이 이미 응답 중일 때 온 메시지"가 조용히 로그에서 누락됨)
        if not is_command and not is_feed_message:
            if is_linked_channel and is_kakao:
                log_message(user, content, direction="in")
            # TODO: DISCORD_LOG_CHANNEL_IDS(순수 디스코드 로그 채널)용 대칭 로그 저장 함수

        # 호출어("애순아"/"애순이"/"애순") - 감지되면 확률/쿨다운 없이 무조건 응답
        call_word = autonomous_reply.detect_call_word(content)
        if call_word:
            prompt = autonomous_reply.strip_call_word(content)
            autonomous_reply.mark_active(key)
            try:
                reply = GREETING_REPLY if not prompt else await self._generate(message, prompt)
                await self._reply(message, user, is_kakao, reply)
            except Exception:
                # LLM/LiteLLM 인증 실패, 타임아웃 등 - 조용히 실패하지 않고 최소한 사용자에게 알린다.
                log.exception("호출어 응답 생성 실패 (channel=%s)", message.channel.id)
                await self._safe_reply(message, FAILURE_REPLY)
            finally:
                autonomous_reply.clear_active(key)
            return

        # 명령어(/)가 아닌 일반 대화일 때만 스킵/자율응답 판단
        if not is_command:
            if await self._should_skip(message, key):
                return

        if is_command:
            return  # 새 봇은 discord.py의 진짜 슬래시 명령(app_commands)이 별도로 처리

        if not autonomous_reply.should_auto_reply(content):
            return

        autonomous_reply.mark_active(key)
        try:
            reply = await self._generate(message, content)
            await self._reply(message, user, is_kakao, reply)
        except Exception:
            # 자율 응답은 원래 확률적으로 참견하는 거라, 실패했다고 채널에 에러 메시지까지
            # 남기면 오히려 더 어색하다 - 로그만 남기고 조용히 넘어간다.
            log.exception("자율 응답 생성 실패 (channel=%s)", message.channel.id)
        finally:
            autonomous_reply.clear_active(key)

    async def _safe_reply(self, message: Message, text: str) -> None:
        try:
            await message.reply(text)
        except discord.HTTPException:
            log.exception("실패 메시지 전송조차 실패 (channel=%s)", message.channel.id)

    async def _generate(self, message: Message, prompt: str) -> str:
        async with message.channel.typing():
            return await a_query(prompt)

    async def _reply(self, message: Message, user: UserRef, is_kakao: bool, reply: str) -> None:
        await message.reply(reply)
        if is_kakao:
            room_id = user.raw_id.split("//", 1)[0]
            log_message(user, reply, direction="out")
            await send_message(room_id, reply)

    async def _should_skip(self, message: Message, key: str) -> bool:
        """이미 응답 생성 중이거나 직전 메시지가 봇 메시지면 건너뜀 (기존 _should_skip 이식)."""
        if autonomous_reply.is_active(key):
            return True
        try:
            history = [m async for m in message.channel.history(limit=2)]
        except discord.HTTPException:
            return False
        if len(history) > 1:
            prev = history[1]
            if prev.author.bot and prev.webhook_id is None:
                return True
        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat(bot))
