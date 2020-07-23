import logging
import time
import re

import discord
from discord.ext import commands, menus

from objects.errors import TemplateNotFoundError, CanvasNotSupportedError
from utils import checks, GlimmerArgumentParser, FactionAction, sqlite as sql
from extensions.template.utils import CheckerSource

log = logging.getLogger(__name__)


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='alert')
    async def alert(self, ctx, name, channel: discord.TextChannel = None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        if template.canvas != "pixelcanvas":
            raise CanvasNotSupportedError()

        mute = sql.mute_get(template.id)
        if mute:
            sql.mute_remove(template.id)
            alert_id, _, _ = mute
            mute_channel = self.bot.get_channel(alert_id)
            await ctx.send(f"Mute for `{name}` in {mute_channel.mention} cleared.")

        if channel:
            sql.template_kwarg_update(ctx.guild.id, name, alert_id=channel.id)
            await ctx.send(f"`{name}` will now alert in the channel {channel.mention} when damaged.")
        else:
            sql.template_remove_alert(template.id)
            await ctx.send(f"`{name}` will no longer alert for damage.")

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='mute', aliases=['m'])
    async def mute(self, ctx, name, duration=None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        if template.canvas != "pixelcanvas":
            raise CanvasNotSupportedError()

        if not duration:
            if template.alert_id:
                return await ctx.send(f"`{name}` is not currently muted.")

            sql.mute_remove(template.id)
            await ctx.send(f"Unmuted `{name}`.")
        else:
            try:
                duration = float(duration) * 3600
            except ValueError:
                matches = re.findall(r"(\d+[wdhms])", duration.lower())  # Week Day Hour Minute Second

                if not matches:
                    return await ctx.send("Invalid mute duration, give the number of hours or format like `1h8m`")

                suffixes = [match[-1] for match in matches]
                if len(suffixes) != len(set(suffixes)):
                    return await ctx.send("Invalid mute duration, duplicate time suffix (eg: 1**h**8m3**h**)")

                seconds = {
                    "w": 7 * 24 * 60 * 60,
                    "d": 24 * 60 * 60,
                    "h": 60 * 60,
                    "m": 60,
                    "s": 1
                }

                duration = sum([int(match[:-1]) * seconds.get(match[-1]) for match in matches])

            if not template.alert_id:
                return await ctx.send(f"`{name}` has no alert channel/is already muted.")

            sql.mute_add(ctx.guild.id, template, time.time() + duration)
            await ctx.send(f"`{name}` muted for {duration / 3600:.2f} hours.")

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @commands.command(name="recent")
    async def recent(self, ctx, *args):
        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-p", "--page", type=int, default=1)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        templates = sql.template_get_all_by_guild_id(gid)
        checker_templates = [t for id, t in self.bot.pcio.templates.items() if id in [t_.id for t_ in templates]]
        pixels = [p for t in checker_templates for p in t.pixels if p.fixed is False]
        pixels = list(dict.fromkeys(pixels))  # Remove duplicates because fuck this codebase with a rusty screwdriver, good god I hate it
        pixels.sort(key=lambda p: p.recieved, reverse=True)

        if not pixels:
            await ctx.send("No recent errors found.")
            return

        checker_menu = menus.MenuPages(
            source=CheckerSource(pixels, checker_templates),
            clear_reactions_after=True,
            timeout=300.0)
        checker_menu.current_page = max(min(args.page - 1, checker_menu.source.get_max_pages()), 0)
        try:
            await checker_menu.start(ctx, wait=True)
            checker_menu.source.embed.set_footer(text=ctx.s("bot.timeout"))
            await checker_menu.message.edit(embed=checker_menu.source.embed)
        except discord.NotFound:
            await ctx.send(ctx.s("bot.menu_deleted"))
