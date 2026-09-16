"""
/그림: 기본 백엔드(config의 IMAGE_GEN_BACKEND, 기본 Horde)로 생성.
/그림스타일: 사용자가 모델/스타일명을 직접 지정 (Horde의 models 파라미터로 전달됨).

기존 봇은 이미지 생성 로직이 horde_gen.py(명령어용)와 aesun_img_gen.py(자율대화용)로
중복 구현되어 있었다. 여기서는 ai/image_engine.py 하나만 호출해서 그 문제를 없앤다.
동시성은 core/concurrency.py의 워커풀로 제한한다 (기존 봇의 동시요청 막힘 버그 대응).
"""
from __future__ import annotations

import io
import logging

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from ai.backends.base import ImageGenError
from ai.image_engine import generate_image
from core.concurrency import image_gen_pool
from core.user_ref import UserRef

log = logging.getLogger("image_gen")


class ImageGen(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="그림", description="이미지를 생성합니다")
    async def generate(self, interaction: Interaction, prompt: str):
        log.info("/그림 수신 (user=%s, channel=%s, prompt=%r)", interaction.user.id, interaction.channel_id, prompt)
        await interaction.response.defer()
        log.info("/그림 defer 완료 - 큐에 제출합니다")
        user = UserRef.from_discord(interaction.user.id, interaction.user.display_name)

        async def job():
            log.info("/그림 워커가 작업을 시작합니다 (user=%s)", user.key)
            return await generate_image(prompt)

        await self._run_and_reply(interaction, user, job, prompt)

    @app_commands.command(name="그림스타일", description="스타일(모델)을 지정해 이미지를 생성합니다")
    async def generate_with_style(self, interaction: Interaction, prompt: str, style: str):
        log.info("/그림스타일 수신 (user=%s, prompt=%r, style=%r)", interaction.user.id, prompt, style)
        await interaction.response.defer()
        user = UserRef.from_discord(interaction.user.id, interaction.user.display_name)

        async def job():
            log.info("/그림스타일 워커가 작업을 시작합니다 (user=%s)", user.key)
            return await generate_image(prompt, style=style)

        await self._run_and_reply(interaction, user, job, prompt)

    async def _run_and_reply(self, interaction: Interaction, user: UserRef, job, prompt: str):
        try:
            result = await image_gen_pool.submit(user, job)
        except ImageGenError as exc:
            await self._safe_followup(interaction, f"이미지 생성 실패: {exc}")
            return
        except Exception:
            # HORDE_API_KEY 누락, 네트워크 오류 등 ImageGenError로 안 감싸진 예외까지 전부 잡아서
            # 최소한 "조용히 2분째 무반응"은 안 나게 한다.
            log.exception("이미지 생성 중 예상 못한 예외 (user=%s, prompt=%s)", user.key, prompt)
            await self._safe_followup(interaction, "이미지 생성 중 예상치 못한 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
            return

        log.info("/그림 생성 완료 - 디스코드로 전송합니다 (backend=%s, model=%s)", result.backend, result.model)
        await self._safe_followup(
            interaction, content=f"`{prompt}` ({result.backend}/{result.model})", image_bytes=result.image_bytes
        )

    async def _safe_followup(
        self, interaction: Interaction, content: str, image_bytes: bytes | None = None
    ):
        """
        슬래시 명령어의 followup 토큰은 최대 15분(900초)까지만 유효하다. 이미지 생성
        타임아웃(MAX_WAIT_SEC)을 그보다 길게 잡아둔 상태라, 15분 넘게 걸리면 결과가 나와도
        followup 전송 자체가 실패할 수 있다 - 그 경우 채널에 직접 메시지로라도 보낸다
        (사용자를 언급해서 누구에게 온 결과인지 알 수 있게).

        discord.File은 한 번 전송하면 내부 스트림이 소모되므로, 재시도할 때마다 raw bytes에서
        새로 만들어야 한다 - 그래서 File 객체가 아니라 image_bytes를 받는다.
        """
        try:
            kwargs = {"content": content}
            if image_bytes is not None:
                kwargs["file"] = discord.File(io.BytesIO(image_bytes), filename="generated.png")
            await interaction.followup.send(**kwargs)
        except discord.HTTPException:
            log.warning(
                "followup 전송 실패(토큰 만료 가능성, 15분 제한) - 채널에 직접 전송 시도 (user=%s)",
                interaction.user.id,
            )
            try:
                channel = interaction.channel
                if channel is not None:
                    file = discord.File(io.BytesIO(image_bytes), filename="generated.png") if image_bytes else None
                    await channel.send(content=f"{interaction.user.mention} {content}", file=file)
            except discord.HTTPException:
                log.exception("채널 직접 전송도 실패 (user=%s)", interaction.user.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageGen(bot))
