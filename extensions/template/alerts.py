import logging
import time

from discord import TextChannel
from discord.ext import commands

from objects.errors import TemplateNotFoundError
from utils import checks, sqlite as sql

log = logging.getLogger(__name__)


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='alert')
    async def alert(self, ctx, name, channel: TextChannel = None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        mute = sql.mute_get(template.id)
        if mute:
            sql.mute_remove(template.id)
            await ctx.send(f"Mute for `{name}` in {channel.mention} cleared.")

        if channel:
            sql.template_kwarg_update(ctx.guild.id, name, alert_id=channel.id)
            await ctx.send(f"`{name}` will now alert in the channel {channel.mention} when damaged.")
        else:
            sql.template_remove_alert(template.id)
            await ctx.send(f"`{name}` will no longer alert for damage.")

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='mute', aliases=['m'])
    async def mute(self, ctx, name, duration=None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        if not duration:
            if template.alert_id:
                return await ctx.send(f"`{name}` is not currently muted.")

            sql.mute_remove(template.id)
            await ctx.send(f"Unmuted `{name}`.")
        else:
            try:
                duration = float(duration)
            except ValueError:
                return await ctx.send("Invalid mute duration, please provide a number.")

            if not template.alert_id:
                return await ctx.send(f"`{name}` has no alert channel/is already muted.")

            sql.mute_add(ctx.guild.id, template, time.time() + (duration * 3600))
            await ctx.send(f"`{name}` muted for {duration} hours.")
