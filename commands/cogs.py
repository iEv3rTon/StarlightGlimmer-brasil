import asyncio
import logging
import os

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class Cogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def cog_check(self, ctx):
        return self.bot.is_owner(ctx.author)

    @commands.command(name="reload")
    async def reload_extension(self, ctx, ext_name):
        try:
            self.bot.reload_extension(f"commands.{ext_name}")
            await ctx.send(f"Reloaded {ext_name}.")
        except commands.ExtensionNotLoaded:
            await ctx.send(f"{ext_name} is already unloaded.")
        except commands.ExtensionNotFound:
            await ctx.send(f"{ext_name} could not be found.")

    @commands.command(name="list-extensions", aliases=["le"])
    async def list_extensions(self, ctx):
        out = [name for name, ext in self.bot.extensions.items()]
        await ctx.send(
            embed=discord.Embed().add_field(name="Currently loaded extensions", value="\n".join(out)))

    @commands.command(name="git-pull", aliases=["pull"])
    async def git_pull(self, ctx):
        if "glimmer.py" not in os.listdir():
            await ctx.send("Not in the right directory, I cannot pull from git.")
            return

        process = await asyncio.create_subprocess_shell("git pull", stdout=asyncio.subprocess.PIPE)
        stdout, _ = await process.communicate()
        await ctx.send(f"`{stdout.decode()}`")


def setup(bot):
    bot.add_cog(Cogs(bot))
