import logging
import re
import time
import os

import discord
from discord.ext import commands

from objects.bot_objects import GlimContext
from objects.errors import TemplateHttpError
import utils
from utils import config, http, render, sqlite as sql
from utils.version import VERSION


def get_prefix(bot_, msg: discord.Message):
    return [sql.guild_get_prefix_by_id(msg.guild.id), bot_.user.mention + " "] \
        if msg.guild else [config.PREFIX, bot_.user.mention + " "]


class glimmer(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_time = time.time()
        self.fetchers = {
            'pixelcanvas': render.fetch_pixelcanvas,
            'pixelzone': render.fetch_pixelzone,
            'pxlsspace': render.fetch_pxlsspace
        }


log = logging.getLogger(__name__)
bot = glimmer(
    command_prefix=get_prefix,
    case_insensitive=True,
    owner_id=255376766049320960)
sql.menu_locks_delete_all()


@bot.event
async def on_ready():
    log.info("Starting Starlight Glimmer v{}!".format(VERSION))
    name = "Pixels!"
    type = discord.ActivityType.watching
    await bot.change_presence(status=discord.Status.online, activity=discord.Activity(name=name, type=type))
    if sql.version_get() is None:
        sql.version_init(VERSION)
        is_new_version = False
    else:
        old_version = sql.version_get()
        is_new_version = old_version != VERSION and old_version is not None
        if is_new_version:
            log.info("Database is a previous version. Updating...")
            sql.version_update(VERSION)
            if old_version < 1.6 <= VERSION:
                # Fix legacy templates not having a size
                for t in sql.template_get_all():
                    try:
                        t.size = await render.calculate_size(await http.get_template(t.url, t.name))
                        sql.template_update(t)
                    except TemplateHttpError:
                        log.error("Error retrieving template {0.name}. Skipping...".format(t))

    log.info("Performing guilds check...")
    for g in bot.guilds:
        log.info("'{0.name}' (ID: {0.id})".format(g))
        db_g = sql.guild_get_by_id(g.id)
        if db_g:
            prefix = db_g.prefix if db_g.prefix else config.PREFIX
            if g.name != db_g.name:
                if config.CHANNEL_LOG_GUILD_RENAMES:
                    await utils.channel_log(bot, "Guild **{1}** is now known as **{0.name}** `(ID:{0.id})`".format(g, db_g.name))
                sql.guild_update(g.id, name=g.name)
            if is_new_version:
                ch = next((x for x in g.channels if x.id == db_g.alert_channel), None)
                if ch:
                    data = await http.get_changelog(VERSION)
                    if data:
                        e = discord.Embed(title=data['name'], url=data['url'], color=13594340,
                                          description=data['body']) \
                            .set_author(name=data['author']['login']) \
                            .set_thumbnail(url=data['author']['avatar_url']) \
                            .set_footer(text="Released " + data['published_at'])
                        await ch.send(GlimContext.get_from_guild(g, "bot.update").format(VERSION, prefix), embed=e)
                    else:
                        await ch.send(GlimContext.get_from_guild(g, "bot.update_no_changelog").format(VERSION, prefix))
                    log.info("- Sent update message")
                else:
                    log.info("- Could not send update message: alert channel not found.")
        else:
            j = g.me.joined_at
            if config.CHANNEL_LOG_GUILD_JOINS:
                await utils.channel_log(bot, "Joined guild **{0.name}** (ID: `{0.id}`)".format(g))
            log.info("Joined guild '{0.name}' (ID: {0.id}) between sessions at {1}".format(g, j.timestamp()))
            sql.guild_add(g.id, g.name, int(j.timestamp()))
            await print_welcome_message(g)

    db_guilds = sql.guild_get_all()
    if len(bot.guilds) != len(db_guilds):
        for g in db_guilds:
            if not any(x for x in bot.guilds if x.id == g.id):
                log.info("Kicked from guild '{0}' (ID: {1}) between sessions".format(g.name, g.id))
                if config.CHANNEL_LOG_GUILD_KICKS:
                    await utils.channel_log(bot, "Kicked from guild **{0}** (ID: `{1}`)".format(g.name, g.id))
                sql.guild_delete(g.id)

    log.info('I am ready!')
    await utils.channel_log(bot, "I am ready!")
    print("I am ready!")


@bot.event
async def on_guild_join(guild):
    log.info("Joined new guild '{0.name}' (ID: {0.id})".format(guild))
    if config.CHANNEL_LOG_GUILD_JOINS:
        await utils.channel_log(bot, "Joined new guild **{0.name}** (ID: `{0.id}`)".format(guild))
    sql.guild_add(guild.id, guild.name, int(guild.me.joined_at.timestamp()))
    await print_welcome_message(guild)


@bot.event
async def on_guild_remove(guild):
    log.info("Kicked from guild '{0.name}' (ID: {0.id})".format(guild))
    if config.CHANNEL_LOG_GUILD_KICKS:
        await utils.channel_log(bot, "Kicked from guild **{0.name}** (ID: `{0.id}`)".format(guild))
    sql.guild_delete(guild.id)


@bot.event
async def on_guild_update(before, after):
    if before.name != after.name:
        log.info("Guild {0.name} is now known as {1.name} (ID: {1.id})")
        if config.CHANNEL_LOG_GUILD_RENAMES:
            await utils.channel_log(bot, "Guild **{0.name}** is now known as **{1.name}** (ID: `{1.id}`)".format(before, after))
        sql.guild_update(after.id, name=after.name)


@bot.event
async def on_guild_role_delete(role):
    sql.guild_delete_role(role.id)


@bot.before_invoke
async def on_command_preprocess(ctx):
    invocation_type = "A" if ctx.is_autoscan else "I"
    if ctx.is_default:
        invocation_type += "D"
    if ctx.is_template:
        invocation_type += "T"
    if ctx.is_repeat:
        invocation_type += "R"
    if ctx.guild:
        log.info("[{0}] {1.name}#{1.discriminator} used '{2}' in {3.name} (UID:{1.id} GID:{3.id})"
                 .format(invocation_type, ctx.author, ctx.command.qualified_name, ctx.guild))
    else:
        log.info("[{0}] {1.name}#{1.discriminator} used '{2}' in DM (UID:{1.id})"
                 .format(invocation_type, ctx.author, ctx.command.qualified_name))
    log.info(ctx.message.content)


@bot.event
async def on_message(message):
    # Ignore channels that can't be posted in
    if message.guild and not message.channel.permissions_for(message.guild.me).send_messages:
        return

    # Ignore other bots
    if message.author.bot:
        return

    # Ignore messages from users currently making a menu choice
    locks = sql.menu_locks_get_all()
    for l in locks:
        if message.author.id == l['user_id'] and message.channel.id == l['channel_id']:
            return

    # Ignore messages with spoilered images
    for attachment in message.attachments:
        if attachment.is_spoiler():
            return

    # Ignore messages with any spoilered text
    if re.match(r".*\|\|.*\|\|.*", message.content):
        return

    # Invoke a command if there is one
    ctx = await bot.get_context(message, cls=GlimContext)
    if ctx.invoked_with:
        await bot.invoke(ctx)
        return

    # Autoscan
    await utils.autoscan(ctx)


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

log.info("Loading cogs...")

# loads all extensions from /commands directory
for filename in os.listdir('./extensions'):
    filename = filename[:-3] if filename.endswith(".py") else filename
    extensions = ["animotes", "canvas", "configuration", "faction", "general", "template"]
    if filename in extensions:
        bot.load_extension("extensions.{}".format(filename))


bot.run(config.TOKEN)
