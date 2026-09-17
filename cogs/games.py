"""
미니게임 슬래시 명령어. 카톡 유저는 브릿지로 텍스트만 오기 때문에 슬래시 인터랙션을 쓸 수
없어서, 텍스트로 친 명령(cogs/chat.py)이 카톡+디스코드 양쪽을 다 처리한다. 여기 슬래시
명령어는 디스코드에서 자동완성으로 편하게 쓰기 위한 것이고, 게임 로직은 core/game_engine.py를
그대로 재사용한다 (원본과 동일한 판정/배당/일일제한).

디스코드 네이티브 유저의 room_id/user_id는 game_engine.room_user()의 폴백 규칙과 동일하게
둘 다 author_id를 쓴다 (원본 parse_user_info의 폴백 방식 그대로).
"""
from __future__ import annotations

from discord import Interaction, app_commands
from discord.ext import commands

from core import game_engine


def _room_user(interaction: Interaction) -> tuple[str, str]:
    uid = str(interaction.user.id)
    return uid, uid


class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="가위", description="가위바위보(가위)로 베팅합니다")
    async def rps_scissors(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_rps(room_id, user_id, "가위", 금액))

    @app_commands.command(name="바위", description="가위바위보(바위)로 베팅합니다")
    async def rps_rock(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_rps(room_id, user_id, "바위", 금액))

    @app_commands.command(name="보", description="가위바위보(보)로 베팅합니다")
    async def rps_paper(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_rps(room_id, user_id, "보", 금액))

    @app_commands.command(name="주사위", description="주사위 두 개를 굴려 베팅합니다 (일일 10회)")
    async def dice(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_dice(room_id, user_id, 금액))

    @app_commands.command(name="용호", description="드래곤타이거 - 용/호랑이/무승부 중 선택해서 베팅합니다")
    async def dragontiger(self, interaction: Interaction, 선택: str, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_dragontiger(room_id, user_id, 선택, 금액))

    @app_commands.command(name="다이스포커", description="주사위 5개로 족보를 맞춰 베팅합니다")
    async def dice_poker(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_dice_poker(room_id, user_id, 금액))

    @app_commands.command(name="블랙잭", description="블랙잭 - 21에 가깝게! 베팅합니다")
    async def blackjack(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_blackjack(room_id, user_id, 금액))

    @app_commands.command(name="바카라", description="바카라 - 홀/짝 중 선택해서 베팅합니다")
    async def baccarat(self, interaction: Interaction, 선택: str, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_baccarat(room_id, user_id, 선택, 금액))

    @app_commands.command(name="슬롯머신", description="슬롯머신을 돌려 베팅합니다")
    async def slots(self, interaction: Interaction, 금액: int):
        room_id, user_id = _room_user(interaction)
        await interaction.response.send_message(game_engine.play_slot(room_id, user_id, 금액))


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
