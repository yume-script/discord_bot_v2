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
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        # MCP 서버는 부팅 시 1회만 연결 (mcp_servers.yaml 기반)
        tools = await init_mcp()
        log.info("MCP tools loaded: %s", [t.name for t in tools])

        image_gen_pool.start()

        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            log.info("loaded cog: %s", cog)

        if settings.DISCORD_GUILD_ID:
            guild = discord.Object(id=settings.DISCORD_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self):
        log.info("logged in as %s", self.user)


async def main():
    bot = AesunBot()
    async with bot:
        await bot.start(settings.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
