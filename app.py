import asyncio
import logging

import discord
from discord.ext import commands

from ai.mcp_manager import init_mcp
from config import settings
from core.concurrency import image_gen_pool

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

INITIAL_COGS = [
    "cogs.image_gen",
    "cogs.attendance",
    "cogs.chat",
]


class AesunBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        # SERVER MEMBERS INTENT는 코드에서 실제로 쓰는 곳이 없어서(멤버 목록 캐싱, on_member_join
        # 등을 안 씀) 뺐다 - MESSAGE_CONTENT INTENT만 있으면 된다. message.author.display_name은
        # 멤버 인텐트 없이도 메시지 이벤트에 기본 포함된다.
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # MCP 서버는 부팅 시 1회만 연결 (mcp_servers.yaml 기반)
        tools = await init_mcp()
        log.info("MCP tools loaded: %s", [t.name for t in tools])

        image_gen_pool.start()

        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            log.info("loaded cog: %s", cog)

        # 슬래시 명령어 동기화는 실패해도(권한/스코프 문제 등) 봇 전체가 죽으면 안 된다.
        # setup_hook에서 예외가 나면 discord.py가 로그인 자체를 중단시켜서 재시작 크래시루프에
        # 빠지므로, 여기서만 예외를 잡고 경고 로그를 남긴 뒤 나머지 기동은 계속 진행한다.
        try:
            if settings.DISCORD_GUILD_ID:
                guild = discord.Object(id=settings.DISCORD_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            else:
                await self.tree.sync()
        except discord.HTTPException:
            log.exception(
                "슬래시 명령어 동기화 실패 - 봇 초대 URL에 'applications.commands' 스코프가 "
                "포함됐는지, DISCORD_GUILD_ID가 실제 서버 ID와 일치하는지 확인할 것. "
                "명령어 동기화만 건너뛰고 나머지 기능은 정상 기동한다."
            )

    async def on_ready(self):
        log.info("logged in as %s", self.user)


async def main():
    bot = AesunBot()
    async with bot:
        await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
