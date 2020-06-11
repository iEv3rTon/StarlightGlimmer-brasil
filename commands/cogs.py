import logging
import subprocess

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


def setup(bot):
    bot.add_cog(Cogs(bot))
