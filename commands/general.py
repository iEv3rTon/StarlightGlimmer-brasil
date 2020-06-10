import asyncio
import itertools
import inspect
import logging
from time import time
import datetime
from functools import partial
from fuzzywuzzy import fuzz
import psutil

import discord
from discord.ext import commands
from discord.ext.commands import BucketType, Command, HelpCommand, Group

from lang import en_US, pt_BR, tr_TR
from objects.bot_objects import GlimContext
import utils
from utils import config, http, sqlite as sql
from utils.version import VERSION

log = logging.getLogger(__name__)


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.help_command = GlimmerHelpCommand(command_attrs={"aliases": ["h"]})
        bot.help_command.cog = self

    @commands.command()
    async def changelog(self, ctx):
        data = await http.get_changelog(VERSION)
        if not data:
            await ctx.send(ctx.s("general.err.cannot_get_changelog"))
            return
        e = discord.Embed(title=data['name'], url=data['url'], color=13594340, description=data['body']) \
            .set_author(name=data['author']['login']) \
            .set_thumbnail(url=data['author']['avatar_url']) \
            .set_footer(text="Released {}".format(data['published_at']))
        await ctx.send(embed=e)

    @commands.command()
    async def github(self, ctx):
        await ctx.send("https://github.com/BrickGrass/StarlightGlimmer")

    @commands.command()
    async def invite(self, ctx):
        await ctx.send(config.INVITE)

    @commands.command()
    async def ping(self, ctx):
        ping_start = time()
        ping_msg = await ctx.send(ctx.s("general.ping"))
        await ping_msg.edit(content=ctx.s("general.pong").format(int((time() - ping_start) * 1000)))

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.command(name="stats")
    async def stats(self, ctx):
        system_uptime = datetime.timedelta(seconds=time() - psutil.boot_time())
        bot_uptime = datetime.timedelta(seconds=time() - self.bot.start_time)
        disk = psutil.disk_usage('/')
        mem = psutil.virtual_memory()
        func = partial(psutil.cpu_percent, interval=2, percpu=True)
        cpu = await self.bot.loop.run_in_executor(None, func)

        embed = discord.Embed(description="System statistics")
        embed.add_field(
            name="System uptime",
            value=str(system_uptime).split(".")[0])
        embed.add_field(
            name="Bot uptime",
            value=str(bot_uptime).split(".")[0])
        embed.add_field(
            name="Memory usage",
            value=f"{mem.used/mem.total*100:.0f}% - {mem.used/1000000:.0f}/{mem.total/1000000:.0f} MB")
        embed.add_field(
            name="Disk usage",
            value=f"{disk.used/disk.total*100:.0f}% - {disk.used/1000000:.0f}/{disk.total/1000000:.0f} MB")
        embed.add_field(
            name="CPU usage (per core)",
            value=" - ".join([f"{core}%" for core in cpu]))

        await ctx.send(embed=embed)

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.command(name="suggest")
    async def suggest(self, ctx, *, suggestion: str):
        log.info("Suggestion: {0}".format(suggestion))
        await utils.channel_log(self.bot, "New suggestion from **{0.name}#{0.discriminator}** (ID: `{0.id}`) in guild "
                                          "**{1.name}** (ID: `{1.id}`):".format(ctx.author, ctx.guild))
        await utils.channel_log(self.bot, "> `{}`".format(suggestion))
        await ctx.send(ctx.s("general.suggest"))

    @commands.command(name="version")
    async def version(self, ctx):
        await ctx.send(ctx.s("general.version").format(VERSION))

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.command(name="quickstart")
    async def quickstart(self, ctx):
        sql.menu_locks_add(ctx.channel.id, ctx.author.id)

        need_images = {
            3: "https://cdn.discordapp.com/attachments/561977353283174404/701066183927136296/cen_diamond.png"
        }

        try:
            language = sql.guild_get_language_by_id(ctx.guild.id).lower()
            if language == "en-us":
                tour_steps = [s for s in en_US.STRINGS if s.split(".")[:2] == ["tour", "command"]]
            elif language == "pt-br":
                tour_steps = [s for s in pt_BR.STRINGS if s.split(".")[:2] == ["tour", "command"]]
            elif language == "tr-tr":
                tour_steps = [s for s in tr_TR.STRINGS if s.split(".")[:2] == ["tour", "command"]]

            # Find two templates that fulfill the needs of the guide. If you can't, use these defaults:
            templates = {
                "tl": {"name": "cen_diamond", "faction": "test"},
                "tl_e": {"name": "cen_diamond", "faction": "test"}
            }
            # TODO: <Insert code to find templates here haha>

            await ctx.send(ctx.s("tour.intro"))

            for i, _ in enumerate(tour_steps):
                command = ctx.s(f"tour.command.{i}").format(p=ctx.gprefix, tl=templates["tl"]["name"], tl_e=templates["tl_e"]["name"])
                request = ctx.s("tour.request").format(command)

                img = need_images.get(i)
                if img:
                    request = ctx.s("tour.image").format(request, img)

                await ctx.send(request)
                msg = await quickstart_wait(self.bot, ctx, command, image=img)

                if msg is not False:
                    # invoke the command, ensure that it finishes before sending explaination
                    for _key, value in templates.items():
                        if value["name"] in msg.content:
                            msg.content = f"{msg.content} -f {value['faction']}"
                            break

                    new_ctx = await self.bot.get_context(msg, cls=GlimContext)
                    await self.bot.invoke(new_ctx)

                    await asyncio.sleep(0.5)

                    await ctx.send(embed=discord.Embed().add_field(
                        name=ctx.s("tour.explain"),
                        value=ctx.s(f"tour.explain.{i}").format(ctx.gprefix)))
                else:
                    break
        except Exception as e:
            raise(e)  # Propogate the error to the right handler
        finally:
            # Always cleanup the menu-lock on exit
            await ctx.send(ctx.s("tour.exit"))
            sql.menu_locks_delete(ctx.channel.id, ctx.author.id)


async def quickstart_wait(bot, ctx, next, image=None):
    valid = ["exit", "cancel"]

    def check(m):
        return m.channel == ctx.channel and m.author == ctx.author

    try:
        while True:
            msg = await bot.wait_for("message", check=check)
            if image and len(msg.attachments) == 0:
                await ctx.send(ctx.s("tour.invalid").format(next))
                continue
            if msg.content == next:
                return msg
            elif msg.content in valid:
                return False
            await ctx.send(ctx.s("tour.invalid").format(next))

    except asyncio.TimeoutError:
        pass
    return False


class GlimmerHelpCommand(HelpCommand):

    async def send_bot_help(self, mapping):

        embed = discord.Embed(
            title=self.context.s("general.help_command_list_header"),
            url=self.context.s("general.wiki"))

        filtered = await self.filter_commands(self.context.bot.commands, sort=True, key=GlimmerHelpCommand.get_category)

        for cat, cmds in itertools.groupby(filtered, key=lambda command: command.cog_name):
            cmds = sorted(cmds, key=lambda x: x.name)
            if len(cmds) > 0:
                commands = []
                for c in cmds:
                    url = "{}{}#{}".format(
                        self.context.s("general.wiki"),
                        GlimmerHelpCommand.get_category(c),
                        c.qualified_name)
                    commands.append(f"[{c.name}]({url})")
                embed.add_field(name=cat, value=", ".join(commands))

        embed.set_footer(text=self.context.s("general.help_footer").format(self.clean_prefix))

        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        embed = await self.generate_help(command)
        await self.get_destination().send(embed=embed)

    async def send_error_message(self, error):
        await self.get_destination().send(error)

    async def send_group_help(self, group):
        embed = await self.generate_help(group)

        await self.get_destination().send(embed=embed)

    def command_not_found(self, string):
        out = self.context.s("general.help_command_not_found").format(string)

        matches = [f"`{c.qualified_name}`" for c in self.context.bot.commands if fuzz.partial_ratio(c.qualified_name, string) >= 70]
        if matches != []:
            m = "{} or {}".format(", ".join(matches[:-1]), matches[-1]) if len(matches) > 1 else matches[0]
            out = "{} {}".format(out, self.context.s("error.did_you_mean").format(m))
        return out

    def subcommand_not_found(self, command, string):
        if isinstance(command, Group) and len(command.all_commands) > 0:
            subcommands = [f"`{c.name}`" for c in command.commands]
            subcommands = self.context.s("bot.or").format(", ".join(subcommands[:-1]), subcommands[-1]) if len(subcommands) > 1 else subcommands[0]
            return self.context.s("general.help_no_subcommand_named").format(command.qualified_name, string, subcommands)
        return self.context.s("general.help_no_subcommands").format(command.qualified_name)

    @staticmethod
    def get_category(command_or_group):
        return f"{command_or_group.cog_name}-Cog"

    async def generate_help(self, command_or_group):
        dot_name = command_or_group.qualified_name.replace(' ', '.')

        embed = discord.Embed(
            title=command_or_group.qualified_name,
            description=self.context.s("brief." + dot_name),
            url="{}{}#{}".format(
                self.context.s("general.wiki"),
                GlimmerHelpCommand.get_category(command_or_group),
                command_or_group.qualified_name.replace(" ", "-")))

        sig = self.context.s("signature." + dot_name)
        if isinstance(sig, list):
            usage = ["`{}{} {}`".format(self.clean_prefix, command_or_group.qualified_name, x) for x in sig]
        elif sig is not None:
            usage = ["`{}{} {}`".format(self.clean_prefix, command_or_group.qualified_name, sig)]
        else:
            usage = ["`{}{}`".format(self.clean_prefix, command_or_group.qualified_name)]
        embed.add_field(name=self.context.s("bot.usage"), value="\n".join(usage), inline=False)

        if len(command_or_group.aliases) > 0:
            embed.add_field(
                name=self.context.s("bot.aliases"),
                value="\n".join(["`{}`".format(a) for a in command_or_group.aliases]),
                inline=False)

        # <long doc> section
        long_doc = self.context.s("help." + dot_name)
        if long_doc:
            embed.add_field(
                name=self.context.s("general.help_more_info"),
                value="{}".format(inspect.cleandoc(long_doc)).format(p=self.clean_prefix),
                inline=False)

        if isinstance(command_or_group, Group):
            filtered = await self.filter_commands(command_or_group.commands, sort=True)
            s = []
            for cmd in filtered:
                s.append('`{0}` - {1}'.format(
                    cmd.name,
                    self.context.s('brief.' + cmd.qualified_name.replace(' ', '.'))))
            embed.add_field(
                name=self.context.s("bot.subcommands"),
                value="\n".join(s),
                inline=False)

        args = self.context.s("args." + dot_name)
        args2 = self.context.s("args." + dot_name + "2")
        if args:
            embed.add_field(
                name=self.context.s("general.help_arguments"),
                value="{}".format(inspect.cleandoc(args)).format(p=self.clean_prefix),
                inline=False)
        if args2:
            embed.add_field(
                name=self.context.s("general.help_arguments") + " 2",
                value="{}".format(inspect.cleandoc(args2)).format(p=self.clean_prefix),
                inline=False)

        examples = self.context.s("example." + dot_name)
        if examples:
            e = []
            for ex in self.context.s("example." + dot_name):
                e.append("`{}{} {}` {}".format(self.clean_prefix, command_or_group.qualified_name, *ex))
            embed.add_field(
                name=self.context.s("bot.examples"),
                value="\n".join(e),
                inline=False)

        embed.set_footer(text=self.context.s("general.help_footer").format(self.clean_prefix))

        return embed


def setup(bot):
    bot.add_cog(General(bot))