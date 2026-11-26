"""
관리자 전용 머니 조정 명령어. settings.ADMIN_DISCORD_IDS에 등록된 디스코드 유저ID만 사용 가능.
카톡 릴레이 메시지는 브릿지 계정이 author라서 관리자 판별이 불가능하므로, 이 명령은 디스코드에서
직접 실행할 때만(슬래시든 텍스트든) 동작한다.
"""
from __future__ import annotations

from discord import Interaction, Member, app_commands
from discord.app_commands import Choice
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


    @app_commands.command(name="mcp상태", description="[관리자] 디스코드봇의 MCP 서버 연결 상태 (부팅 때 결과 + 지금 물어보기)")
    @app_commands.describe(확인="지금 각 서버에 실제로 물어볼지 (몇 초~수십 초 걸림)")
    @app_commands.choices(확인=[Choice(name="부팅 때 결과만 (빠름)", value="boot"), Choice(name="지금 물어보기", value="ping")])
    async def mcp_status(self, interaction: Interaction, 확인: str = "boot"):
        if not is_admin(interaction.user.id):
            await interaction.response.send_message("🚫 관리자만 사용할 수 있는 명령이에요.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        from ai import mcp_manager
        pings = await mcp_manager.ping_all() if 확인 == "ping" else {}
        text = mcp_status_text(mcp_manager.status_rows(), pings)
        await interaction.followup.send(text[:1990], ephemeral=True)


def mcp_status_text(rows: list[dict], pings: dict | None = None) -> str:
    """/mcp상태 글: 서버마다 부팅 때 결과(도구 수·오류), 게이트웨이 경유, 열어 둔 세션, (확인했으면) 지금 응답"""
    pings = pings or {}
    if not rows:
        return "🔌 **MCP 서버** - 설정된 서버가 없거나 아직 부팅 중이에요."
    ok_n = sum(1 for r in rows if r["ok"] and not r["gateway_of"])
    lines = [f"🔌 **MCP 서버** {ok_n}/{sum(1 for r in rows if not r['gateway_of'])} 연결" + (" · 지금 물어봄" if pings else " · 부팅 때 결과")]
    for r in rows:
        if r["gateway_of"]:
            lines.append(f"{'✅' if r['ok'] else '❌'} **{r['name']}** (LiteLLM 게이트웨이 - {', '.join(r['gateway_of'])})"
                         + ("" if r["ok"] else f" - {r['error']}"))
            continue
        head = f"{'✅' if r['ok'] else '❌'} **{r['name']}**" + (f" (게이트웨이 {r['via']} 경유)" if r["via"] else "")
        bits = [f"도구 {r['tools']}개"] if r["ok"] else [r["error"] or "연결 실패"]
        if r["kept"]:
            bits.append(f"세션: {r['kept']}")
        p = pings.get(r["name"])
        if p:
            bits.append(f"지금: {'응답' if p[0] else '❌ ' + p[1]} {p[2]:.1f}초" + (f" ({p[1]})" if p[0] else ""))
        lines.append(f"{head} - " + " · ".join(bits))
    return "\n".join(lines)


async def setup(bot: commands.Bot):
    await bot.add_cog(Admin(bot))
