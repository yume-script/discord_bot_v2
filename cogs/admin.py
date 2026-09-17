"""
관리자 전용 머니 조정 명령어. settings.ADMIN_DISCORD_IDS에 등록된 디스코드 유저ID만 사용 가능.
카톡 릴레이 메시지는 브릿지 계정이 author라서 관리자 판별이 불가능하므로, 이 명령은 디스코드에서
직접 실행할 때만(슬래시든 텍스트든) 동작한다.
"""
from __future__ import annotations

from discord import Interaction, Member, app_commands
from discord.ext import commands

from core.admin_auth import is_admin
from core.money_system import money_system


class Admin(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="머니설정", description="[관리자] 디스코드 유저의 잔액을 원하는 금액으로 설정합니다")
    async def set_balance(self, interaction: Interaction, 대상: Member, 금액: int):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        room_id = user_id = str(대상.id)
        ok, result = money_system.set_balance(
            room_id, user_id, 금액, description=f"관리자({interaction.user.display_name}) 설정"
        )
        if ok:
            await interaction.response.send_message(f"✅ {대상.display_name}님의 잔액을 {금액:,}원으로 설정했어요.")
        else:
            await interaction.response.send_message(f"❌ 오류: {result}")

    @app_commands.command(name="머니설정카톡", description="[관리자] 카톡 유저(방ID/유저ID)의 잔액을 설정합니다")
    async def set_balance_kakao(self, interaction: Interaction, 방id: str, 유저id: str, 금액: int):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        ok, result = money_system.set_balance(
            방id, 유저id, 금액, description=f"관리자({interaction.user.display_name}) 설정"
        )
        if ok:
            await interaction.response.send_message(f"✅ 카톡 유저({방id}/{유저id}) 잔액을 {금액:,}원으로 설정했어요.")
        else:
            await interaction.response.send_message(f"❌ 오류: {result}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
