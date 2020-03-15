import itertools
import inspect
import logging
from time import time

import discord
from discord.ext import commands
from discord.ext.commands import BucketType, Command, HelpCommand

import utils
from utils import config, http
from utils.version import VERSION

log = logging.getLogger(__name__)


class General(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        bot.help_command = GlimmerHelpCommand()
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
            .set_footer(text="Released " + data['published_at'])
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
        ping_time = time() - ping_start
        log.info("(Ping:{0}ms)".format(int(ping_time * 1000)))
        await ping_msg.edit(content=ctx.s("general.pong").format(int(ping_time * 1000)))

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.command()
    async def suggest(self, ctx, *, suggestion: str):
        log.info("Suggestion: {0}".format(suggestion))
        await utils.channel_log(self.bot, "New suggestion from **{0.name}#{0.discriminator}** (ID: `{0.id}`) in guild "
                              "**{1.name}** (ID: `{1.id}`):".format(ctx.author, ctx.guild))
        await utils.channel_log(self.bot, "> `{}`".format(suggestion))
        await ctx.send(ctx.s("general.suggest"))

    @commands.command()
    async def version(self, ctx):
        await ctx.send(ctx.s("general.version").format(VERSION))

class GlimmerHelpCommand(HelpCommand):

    async def send_bot_help(self, mapping):

        embed = discord.Embed(
            title=self.context.s("general.help_command_list_header"),
            url=self.context.s("general.wiki"))

        def get_category(command: Command):
            return {
                'General':       '1. General',
                'Canvas':        '2. Canvas',
                'Template':      '3. Template',
                'Faction':       '4. Faction',
                'Animotes':      '5. Animotes',
                'Configuration': '6. Configuration'
            }[command.cog_name]

        filtered = await self.filter_commands(self.context.bot.commands, sort=True, key=get_category)

        for cat, cmds in itertools.groupby(filtered, key=get_category):
            cmds = sorted(cmds, key=lambda x: x.name)
            if len(cmds) > 0:
                cmds = ", ".join([f"`{c.name}`" for c in cmds])
                embed.add_field(name=cat, value=cmds)

        embed.set_footer(text=self.context.s("general.help_footer").format(self.clean_prefix))

        await self.get_destination().send(embed=embed)

    async def send_cog_help(self, cog):
        pass  # TODO

    async def send_command_help(self, command):
        embed = self.generate_help(command)
        await self.get_destination().send(embed=embed)

    async def send_error_message(self, error):
        pass  # TODO

    async def send_group_help(self, group):
        embed = self.generate_help(group)

        filtered = await self.filter_commands(group.commands, sort=True)
        s = []
        for cmd in filtered:
            s.append('`{0}` - {1}'.format(
                cmd.name,
                self.context.s('brief.' + cmd.qualified_name.replace(' ', '.'))))
        embed.insert_field_at(
            index=-2,
            name=self.context.s("bot.subcommands"),
            value="\n".join(s),
            inline=False)

        await self.get_destination().send(embed=embed)

    @staticmethod
    def get_category(command_or_group):
        return {
            'General':       'General-Cog',
            'Canvas':        'Canvas-Cog',
            'Template':      'Template-Cog',
            'Faction':       'Faction-Cog',
            'Animotes':      'Animotes-Cog',
            'Configuration': 'Configuration-Cog'
        }[command_or_group.cog_name]

    def generate_help(self, command_or_group):
        dot_name = command_or_group.qualified_name.replace(' ', '.')

        embed = discord.Embed(
            title=command_or_group.qualified_name,
            description=self.context.s("brief." + dot_name),
            url="{}{}#{}".format(
                self.context.s("general.wiki"),
                self.get_category(command_or_group),
                command_or_group.qualified_name))

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

        args = self.context.s("args." + dot_name)
        if args:
            embed.add_field(
                name=self.context.s("general.help_arguments"),
                value="{}".format(inspect.cleandoc(args)).format(p=self.clean_prefix),
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
