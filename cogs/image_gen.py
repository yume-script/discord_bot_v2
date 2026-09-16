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
        await interaction.response.defer()
        user = UserRef.from_discord(interaction.user.id, interaction.user.display_name)

        async def job():
            return await generate_image(prompt)

        await self._run_and_reply(interaction, user, job, prompt)

    @app_commands.command(name="그림스타일", description="스타일(모델)을 지정해 이미지를 생성합니다")
    async def generate_with_style(self, interaction: Interaction, prompt: str, style: str):
        await interaction.response.defer()
        user = UserRef.from_discord(interaction.user.id, interaction.user.display_name)

        async def job():
            return await generate_image(prompt, style=style)

        await self._run_and_reply(interaction, user, job, prompt)

    async def _run_and_reply(self, interaction: Interaction, user: UserRef, job, prompt: str):
        try:
            result = await image_gen_pool.submit(user, job)
        except ImageGenError as exc:
            await interaction.followup.send(f"이미지 생성 실패: {exc}")
            return
        except Exception:
            # HORDE_API_KEY 누락, 네트워크 오류 등 ImageGenError로 안 감싸진 예외까지 전부 잡아서
            # 최소한 "조용히 2분째 무반응"은 안 나게 한다.
            log.exception("이미지 생성 중 예상 못한 예외 (user=%s, prompt=%s)", user.key, prompt)
            await interaction.followup.send("이미지 생성 중 예상치 못한 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
            return

        file = discord.File(io.BytesIO(result.image_bytes), filename="generated.png")
        await interaction.followup.send(
            content=f"`{prompt}` ({result.backend}/{result.model})", file=file
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ImageGen(bot))
