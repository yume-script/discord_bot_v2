"""
/날씨, /전국날씨, /환율, /주식, /운세, /mbti - core/ai 쪽 함수를 그대로 호출한다.
자연어로 아메하나에게 직접 물어봤을 때(ai/rag_engine.py)도 날씨/환율/주식은 같은 함수를 쓰기
때문에, 명령어로 조회하든 대화로 물어보든 항상 같은 데이터가 나온다.
"""
from __future__ import annotations

from discord import Interaction, app_commands
from discord.ext import commands

from ai.local_tools import get_exchange_rate, get_nationwide_weather, get_stock_price, get_weather
from core import fortune, katalk_stats, mbti


class Lookup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="날씨", description="지역 날씨를 알려줘요")
    async def weather(self, interaction: Interaction, 지역: str = "서울"):
        await interaction.response.defer()
        result = await get_weather.ainvoke({"location": 지역})
        await interaction.followup.send(result)

    @app_commands.command(name="전국날씨", description="전국 주요 도시 날씨를 한번에 알려줘요")
    async def nationwide_weather(self, interaction: Interaction):
        await interaction.response.defer()
        result = await get_nationwide_weather.ainvoke({})
        await interaction.followup.send(result)

    @app_commands.command(name="환율", description="원화 기준 환율을 알려줘요 (예: USD, JPY, EUR)")
    async def exchange(self, interaction: Interaction, 통화: str = "USD"):
        await interaction.response.defer()
        result = await get_exchange_rate.ainvoke({"currency": 통화})
        await interaction.followup.send(result)

    @app_commands.command(name="주식", description="주식 현재가를 알려줘요 (예: 005930.KS, AAPL)")
    async def stock(self, interaction: Interaction, 티커: str):
        await interaction.response.defer()
        result = await get_stock_price.ainvoke({"ticker": 티커})
        await interaction.followup.send(result)

    @app_commands.command(name="운세", description="오늘의 운세를 알려줘요 (예: 양띠, 사자자리)")
    async def fortune_cmd(self, interaction: Interaction, 질의: str):
        await interaction.response.defer()
        result = await fortune.get_fortune(질의)
        await interaction.followup.send(result)

    @app_commands.command(name="mbti", description="이 채널 대화 기록을 바탕으로 MBTI를 분석해요 (대상 생략 시 본인)")
    async def mbti_cmd(self, interaction: Interaction, 대상: str | None = None):
        await interaction.response.defer()
        conversation_key = f"discord:{interaction.channel_id}"
        target_name = 대상 or interaction.user.display_name
        result = await mbti.analyze_mbti(conversation_key, target_name)
        await interaction.followup.send(result)

    @app_commands.command(name="월간카톡", description="이번 달 이 채널의 대화 통계를 보여줘요")
    async def monthly_katalk(self, interaction: Interaction):
        await interaction.response.defer()
        conversation_key = f"discord:{interaction.channel_id}"
        await interaction.followup.send(katalk_stats.get_monthly_stats(conversation_key))

    @app_commands.command(name="카톡순위", description="채팅 순위를 보여줘요 (오늘/이번달)")
    async def katalk_rank(self, interaction: Interaction, 기간: str = "오늘"):
        await interaction.response.defer()
        conversation_key = f"discord:{interaction.channel_id}"
        period = "month" if 기간 in ("이번달", "이번 달", "월") else "today"
        await interaction.followup.send(katalk_stats.get_ranking(conversation_key, period))

    @app_commands.command(name="오늘대화요약", description="오늘 이 채널에서 오간 대화를 요약해줘요")
    async def today_summary(self, interaction: Interaction):
        await interaction.response.defer()
        conversation_key = f"discord:{interaction.channel_id}"
        result = await katalk_stats.summarize_today(conversation_key)
        await interaction.followup.send(result)


async def setup(bot: commands.Bot):
    await bot.add_cog(Lookup(bot))
