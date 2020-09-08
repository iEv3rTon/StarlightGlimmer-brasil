import datetime
import asyncio
import logging
import re
import time
import argparse
from functools import partial

import discord
from discord.ext import menus, commands
from discord.utils import get as dget

from objects.errors import NoAttachmentError, NoJpegsError, NotPngError, FactionNotFoundError, ColorError, BadArgumentErrorWithMessage
from utils import config
from objects.database_models import Guild

log = logging.getLogger(__name__)


async def autoscan(ctx):
    if not ctx.guild:
        return

    guild = ctx.session.query(Guild).get(ctx.guild.id)
    if not guild.autoscan:
        return

    canvas = guild.canvas if guild.canvas else "pixelcanvas"

    cmd = None
    g = None
    m_pc = re.search(r'pixelcanvas\.io/@(-?\d+),(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_pz = re.search(r'pixelzone\.io/\?p=(-?\d+),(-?\d+)(?:,(\d+))?(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_ps = re.search(r'pxls\.space/#x=(\d+)&y=(\d+)(?:&scale=(\d+))?(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_pre_def = re.search(r'@(-?\d+)(?: |,|, )(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_dif_def = re.search(r'(-?\d+)(?: |,|, )(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    if m_pc:
        cmd = dget(dget(ctx.bot.commands, name='preview').commands, name='pixelcanvas')
        g = m_pc.groups()
    elif m_pz:
        cmd = dget(dget(ctx.bot.commands, name='preview').commands, name='pixelzone')
        g = m_pz.groups()
    elif m_ps:
        cmd = dget(dget(ctx.bot.commands, name='preview').commands, name='pxlsspace')
        g = m_ps.groups()
    elif m_pre_def:
        cmd = dget(dget(ctx.bot.commands, name='preview').commands, name=canvas)
        g = m_pre_def.groups()
    elif m_dif_def and len(ctx.message.attachments) > 0 and ctx.message.attachments[0].filename[-4:].lower() == ".png":
        cmd = dget(dget(ctx.bot.commands, name='diff').commands, name=canvas)
        g = m_dif_def.groups()

    if cmd:
        view = f"{g[0]} {g[1]} -z {g[2] if g[2] != None else 1}"
        ctx.command = cmd
        ctx.view = commands.StringView(view)
        ctx.is_autoscan = True
        await ctx.bot.invoke(ctx)
        return True


async def channel_log(bot, msg):
    if config.LOGGING_CHANNEL_ID:
        channel = bot.get_channel(config.LOGGING_CHANNEL_ID)
        if not channel:
            log.warning("Can't find logging channel")
        else:
            try:
                await channel.send("`{}` {}".format(time.strftime('%H:%M:%S', time.localtime()), msg))
            except discord.errors.Forbidden:
                log.warning("Forbidden from logging channel!")


def get_botadmin_role(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    r = dget(ctx.guild.roles, id=guild.bot_admin)
    if guild.bot_admin and not r:
        guild.bot_admin = None
        return None
    return r


def get_templateadmin_role(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    r = dget(ctx.guild.roles, id=guild.template_admin)
    if guild.template_admin and not r:
        guild.template_admin = None
        return None
    return r


def get_templateadder_role(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    r = dget(ctx.guild.roles, id=guild.template_adder)
    if guild.template_adder and not r:
        guild.template_adder = None
        return None
    return r


def is_admin(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    r = dget(
        ctx.author.roles,
        id=guild.bot_admin
    )
    return bool(r) or ctx.author.permissions_in(ctx.channel).administrator


def is_template_admin(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    r = dget(
        ctx.author.roles,
        id=guild.template_admin
    )
    return bool(r)


def is_template_adder(ctx):
    guild = ctx.session.query(Guild).get(ctx.guild.id)
    role_id = guild.template_adder
    r = dget(ctx.author.roles, id=role_id)
    return bool(not role_id or r)


async def verify_attachment(ctx):
    if len(ctx.message.attachments) < 1:
        raise NoAttachmentError
    att = ctx.message.attachments[0]
    if att.filename[-4:].lower() != ".png":
        if att.filename[-4:].lower() == ".jpg" or att.filename[-5:].lower() == ".jpeg":
            raise NoJpegsError
        raise NotPngError
    return att


class YesNoMenu(menus.Menu):
    def __init__(self, msg):
        super().__init__(timeout=480, delete_message_after=True)
        self.msg = msg
        self.result = None

    async def send_initial_message(self, ctx, channel):
        emoji_text = {
            "🆘": ctx.s("bot.cancel"),
            "❌": ctx.s("bot.no"),
            "✅": ctx.s("bot.yes")
        }

        out = [f"{emoji.name} - {emoji_text.get(emoji.name)}" for emoji, _ in self.buttons.items()]

        self.embed = discord.Embed()
        self.embed.add_field(name="Question", value=self.msg, inline=False)
        self.embed.add_field(name="Options", value="\n".join(out), inline=False)
        return await channel.send(embed=self.embed)

    @menus.button('✅')
    async def do_yes(self, payload):
        if payload.user_id == self.ctx.author.id:
            self.result = True
            self.stop()

    @menus.button('❌')
    async def do_no(self, payload):
        if payload.user_id == self.ctx.author.id:
            self.result = False
            self.stop()

    async def prompt(self, ctx):
        await self.start(ctx, wait=True)
        return self.result


class YesNoCancelMenu(YesNoMenu):
    @menus.button('🆘')
    async def do_cancel(self, payload):
        if payload.user_id == self.ctx.author.id:
            self.stop()


async def yes_no(ctx, question, cancel=False):
    if cancel:
        answer = await YesNoCancelMenu(question).prompt(ctx)
    else:
        answer = await YesNoMenu(question).prompt(ctx)

    if cancel:
        return answer
    return answer if answer is not None else False


def chunkstring(string, length):
    return (string[0 + i:length + i] for i in range(0, len(string), length))


def parse_duration(ctx, duration: str) -> int:
    matches = re.findall(r"(\d+[wdhms])", duration.lower())

    if not matches:
        raise BadArgumentErrorWithMessage(ctx.s("error.invalid_duration_1"))

    matches = {match[-1]: int(match[:-1]) for match in matches}

    suffixes = list(matches.keys())
    if len(suffixes) != len(set(suffixes)):
        raise BadArgumentErrorWithMessage(ctx.s("error.invalid_duration_2"))

    seconds = {
        "w": 7 * 24 * 60 * 60,
        "d": 24 * 60 * 60,
        "h": 60 * 60,
        "m": 60,
        "s": 1
    }

    return sum(num * seconds.get(suffix) for suffix, num in matches.items())


class HelpAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        paginator = commands.Paginator()
        help_txt = parser.format_help()
        for line in help_txt.split("\n"):
            try:
                paginator.add_line(line)
            except RuntimeError:
                for subline in chunkstring(line, paginator.max_size - 10):
                    paginator.add_line(subline)

        for page in paginator.pages:
            parser.loop.create_task(parser.ctx.send(page))
        raise commands.BadArgument  # To exit silently


class MutExcGroup(argparse._MutuallyExclusiveGroup):
    def __init__(self, *args, **kwargs):
        super(MutExcGroup, self).__init__(*args, **kwargs)
        add_argument = partial(self.add_argument, help=" ")
        setattr(self, "add_argument", add_argument)


class GlimmerArgumentParser(argparse.ArgumentParser):
    def __init__(self, ctx):
        command = f"{ctx.prefix}{ctx.invoked_with}"
        super(GlimmerArgumentParser, self).__init__(
            prog=command, add_help=False,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        self.ctx = ctx
        self.loop = asyncio.get_event_loop()

        # Override the default value of add_argument's help kwarg so all default values show up
        # if help=None, ArgumentDefaultsHelpFormatter just ignores the arg in the help text :<
        add_argument = partial(self.add_argument, help=" ")
        setattr(self, "add_argument", add_argument)

        self.add_argument("-h", "--help", action=HelpAction, nargs=0)

    def error(self, message):
        self.loop.create_task(self.ctx.send(f"Error: {message}"))

    def add_mutually_exclusive_group(self, **kwargs):
        group = MutExcGroup(self, **kwargs)
        self._mutually_exclusive_groups.append(group)
        return group


class FactionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        faction = parser.ctx.session.query(Guild).filter_by(faction_name=values).first()
        if not faction:
            faction = parser.ctx.session.query(Guild).filter_by(faction_alias=values.lower()).first()

        if faction is None:
            raise FactionNotFoundError
        setattr(namespace, self.dest, faction)


class ColorAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        try:
            color = abs(int(values, 16) % 0xFFFFFF)
            setattr(namespace, self.dest, color)
        except ValueError:
            raise ColorError


class Duration:
    hour = 60 * 60

    def __init__(self, start, end, input):
        self.start = start
        self.end = end
        self.duration = end - start
        self.input = input

    def __str__(self):
        return self.input

    @property
    def days(self):
        return self.duration.days

    @property
    def hours(self):
        return int(self.duration.total_seconds() / self.hour)


class DurationAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        duration = self.get_duration(parser.ctx, values)
        setattr(namespace, self.dest, duration)

    @staticmethod
    def get_duration(ctx, duration_string):
        end = datetime.datetime.now(datetime.timezone.utc)
        seven_days_ago = end - datetime.timedelta(days=7)

        duration = parse_duration(ctx, duration_string)
        start = end - datetime.timedelta(seconds=duration)

        start = max(start, seven_days_ago)
        return Duration(start, end, duration_string)


async def print_welcome_message(guild):
    channels = (x for x in guild.channels if x.permissions_for(guild.me).send_messages and type(x) is discord.TextChannel)
    c = next((x for x in channels if x.name == "general"), next(channels, None))
    if c:
        await c.send("Hi! I'm {0}. For a full list of commands, pull up my help page with `{1}help`. "
                     "You could also take a quick guided tour of my main features with `{1}quickstart`. "
                     "Happy pixel painting!".format(config.NAME, config.PREFIX))
        log.info("Printed welcome message")
    else:
        log.info("Could not print welcome message: no default channel found")
