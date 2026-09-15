from discord import Interaction, app_commands
from discord.ext import commands


class Attendance(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="출석", description="출석 체크")
    async def attend(self, interaction: Interaction):
        # TODO: 출석 기록 저장 로직
        await interaction.response.send_message(f"{interaction.user.display_name}님 출석 완료!")


async def setup(bot: commands.Bot):
    await bot.add_cog(Attendance(bot))
