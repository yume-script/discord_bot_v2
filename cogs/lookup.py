"""
/날씨, /전국날씨, /환율, /주식 - ai/local_tools.py의 함수를 그대로 호출한다.
자연어로 애순이에게 직접 물어봤을 때(ai/rag_engine.py)도 같은 함수를 쓰기 때문에,
명령어로 조회하든 대화로 물어보든 항상 같은 데이터가 나온다.
"""
from __future__ import annotations

from discord import Interaction, app_commands
from discord.ext import commands

from ai.local_tools import get_exchange_rate, get_nationwide_weather, get_stock_price, get_weather


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


async def setup(bot: commands.Bot):
    await bot.add_cog(Lookup(bot))
