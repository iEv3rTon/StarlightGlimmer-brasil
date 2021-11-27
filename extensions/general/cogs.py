import asyncio
import logging
import os
import subprocess

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class Cogs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_check(self, ctx):
        return await self.bot.is_owner(ctx.author)

    async def cog_command_error(self, ctx, error):
        # Unwrap nested errors
        error = getattr(error, 'original', error)

        try:
            _, _, ext_name = ctx.args
        except IndexError:
            pass

        if isinstance(error, commands.ExtensionNotLoaded):
            await ctx.send(f"{ext_name} is already unloaded.")
        elif isinstance(error, commands.ExtensionAlreadyLoaded):
            await ctx.send(f"{ext_name} is already loaded.")
        elif isinstance(error, commands.ExtensionNotFound):
            await ctx.send(f"{ext_name} could not be found.")
        else:
            return

        error.handled = True

    @commands.command(name="reload")
    async def extension_reload(self, ctx, ext_name):
        self.bot.reload_extension(f"extensions.{ext_name}")
        await ctx.send(f"Reloaded {ext_name}.")

    @commands.command(name="unload")
    async def extension_unload(self, ctx, ext_name):
        self.bot.unload_extension(f"extensions.{ext_name}")
        await ctx.send(f"Unloaded {ext_name}.")

    @commands.command(name="load")
    async def extension_load(self, ctx, ext_name):
        self.bot.load_extension(f"extensions.{ext_name}")
        await ctx.send(f"Loaded {ext_name}.")

    @commands.command(name="list-extensions", aliases=["le"])
    async def list_extensions(self, ctx):
        extensions = [name for name, ext in self.bot.extensions.items()]
        cogs = [name for name, cog in self.bot.cogs.items()]

        extensions.sort()
        cogs.sort()

        embed = discord.Embed(description="Currently loaded extensions/cogs")
        embed.add_field(name="Extensions", value="\n".join(extensions))
        embed.add_field(name="Cogs", value="\n".join(cogs))

        await ctx.send(embed=embed)

    @commands.command(name="git-pull", aliases=["pull"])
    async def git_pull(self, ctx):
        if "glimmer.py" not in os.listdir():
            await ctx.send("Not in the right directory, I cannot pull from git.")
            return

        process = await asyncio.create_subprocess_shell(
            "/usr/bin/git pull",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        streams = await process.communicate()
        streams = [stream.decode() for stream in streams if stream.decode() != ""]
        for stream in streams:
            await ctx.send(f"```{stream}```")

    @commands.command(name="restart", aliases=["restart-bot"])
    async def close_bot(self, ctx):
        await ctx.send("Restarting bot...")
        subprocess.Popen("/usr/bin/sudo /home/pi/Documents/glimmer/restart.sh", shell=True)
