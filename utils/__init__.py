import asyncio
import logging
import re
import time
import argparse

import discord
from discord.ext import menus
from discord.ext.commands.view import StringView
from discord.utils import get as dget

from objects.errors import NoAttachmentError, NoJpegsError, NotPngError, FactionNotFoundError, ColorError
from utils import config, sqlite as sql

log = logging.getLogger(__name__)

async def autoscan(ctx):
    if ctx.guild and not sql.guild_is_autoscan(ctx.guild.id):
        return

    canvas = sql.guild_get_canvas_by_id(ctx.guild.id) if ctx.guild else "pixelcanvas"

    cmd = None
    g = None
    m_pc = re.search('pixelcanvas\.io/@(-?\d+),(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_pz = re.search('pixelzone\.io/\?p=(-?\d+),(-?\d+)(?:,(\d+))?(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_ps = re.search('pxls\.space/#x=(\d+)&y=(\d+)(?:&scale=(\d+))?(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_pre_def = re.search('@(-?\d+)(?: |,|, )(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
    m_dif_def = re.search('(-?\d+)(?: |,|, )(-?\d+)(?:(?: |#| #)(-?\d+))?', ctx.message.content)
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
        ctx.view = StringView(view)
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
    role_id = sql.guild_get_by_id(ctx.guild.id).bot_admin
    r = dget(ctx.guild.roles, id=role_id)
    if role_id and not r:
        sql.guild_update(ctx.guild.id, bot_admin=None)
        return None
    return r


def get_templateadmin_role(ctx):
    role_id = sql.guild_get_by_id(ctx.guild.id).template_admin
    r = dget(ctx.guild.roles, id=role_id)
    if role_id and not r:
        sql.guild_update(ctx.guild.id, template_admin=None)
        return None
    return r


def get_templateadder_role(ctx):
    role_id = sql.guild_get_by_id(ctx.guild.id).template_adder
    r = dget(ctx.guild.roles, id=role_id)
    if role_id and not r:
        sql.guild_update(ctx.guild.id, template_adder=None)
        return None
    return r


def is_admin(ctx):
    role_id = sql.guild_get_by_id(ctx.guild.id).bot_admin
    r = dget(ctx.author.roles, id=role_id)
    return bool(r) or ctx.author.permissions_in(ctx.channel).administrator


def is_template_admin(ctx):
    role_id = sql.guild_get_by_id(ctx.guild.id).template_admin
    r = dget(ctx.author.roles, id=role_id)
    return bool(r)


def is_template_adder(ctx):
    role_id = sql.guild_get_by_id(ctx.guild.id).template_adder
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
        self.result = True
        self.stop()

    @menus.button('❌')
    async def do_no(self, payload):
        self.result = False
        self.stop()

    async def prompt(self, ctx):
        await self.start(ctx, wait=True)
        return self.result


class YesNoCancelMenu(YesNoMenu):
    @menus.button('🆘')
    async def do_cancel(self, payload):
        self.stop()


async def yes_no(ctx, question, cancel=False):
    sql.menu_locks_add(ctx.channel.id, ctx.author.id)
    if cancel:
        answer = await YesNoCancelMenu(question).prompt(ctx)
    else:
        answer = await YesNoMenu(question).prompt(ctx)
    sql.menu_locks_delete(ctx.channel.id, ctx.author.id)

    if cancel:
        return answer if answer else "cancel"
    return answer if answer else False


class GlimmerArgumentParser(argparse.ArgumentParser):

    def __init__(self, ctx):
        argparse.ArgumentParser.__init__(self, add_help=False)
        self.ctx = ctx

    def error(self, message):
        asyncio.ensure_future(self.ctx.send(f"Error: {message}"))


class FactionAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        faction = sql.guild_get_by_faction_name_or_alias(values)
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
