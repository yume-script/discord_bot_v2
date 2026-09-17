"""
/개미소리, /위성사진 - core/ant_voice.py, core/satellite.py를 그대로 호출한다.
"""
from __future__ import annotations

import io

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from core import ant_voice, satellite


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="개미소리", description="개미가 외치는 말풍선 이미지를 만들어요")
    async def ant_voice_cmd(self, interaction: Interaction, 내용: str):
        await interaction.response.defer()
        image_bytes = await ant_voice.generate_ant_voice(내용)
        if image_bytes is None:
            await interaction.followup.send("❌ 이미지 생성에 실패했어요 (에셋을 못 가져왔어요).")
            return
        file = discord.File(io.BytesIO(image_bytes), filename="ant_voice.webp")
        await interaction.followup.send(content=f"🐜 **개미의 외침:** {내용}", file=file)

    @app_commands.command(name="위성사진", description="최신 실시간 위성사진을 보여줘요")
    async def satellite_cmd(self, interaction: Interaction):
        await interaction.response.defer()
        result = await satellite.get_satellite_image()
        if result is None:
            await interaction.followup.send("❌ 위성사진을 가져오지 못했어요. 잠시 후 다시 시도해주세요.")
            return
        image_bytes, obs_time = result
        file = discord.File(io.BytesIO(image_bytes), filename="satellite_latest.png")
        await interaction.followup.send(content=f"📡 천리안 2A호 최신 위성 영상 (관측 시간: {obs_time})", file=file)


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
