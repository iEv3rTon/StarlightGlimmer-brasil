import logging
import math
import time
import os
import re

import discord
from discord.ext import commands, tasks

from objects.bot_objects import GlimContext
from objects.errors import TemplateHttpError
import utils
from utils import checker, config, http, render, sqlite as sql
from utils.version import VERSION


def get_prefix(bot_, msg: discord.Message):
    return [sql.guild_get_prefix_by_id(msg.guild.id), bot_.user.mention + " "] \
        if msg.guild else [config.PREFIX, bot_.user.mention + " "]


class Glimmer(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_time = time.time()
        self.fetchers = {
            'pixelcanvas': render.fetch_pixelcanvas,
            'pixelzone': render.fetch_pixelzone,
            'pxlsspace': render.fetch_pxlsspace
        }

        self.set_presense.start()
        self.unmute.start()
        self.loop.create_task(self.startup())
        self.loop.create_task(self.start_checker())

    @tasks.loop(minutes=30.0)
    async def set_presense(self):
        # https://stackoverflow.com/a/3155023
        def millify(n):
            millnames = ['', 'K', 'M', 'B', 'T']
            n = float(n)
            millidx = max(0, min(len(millnames) - 1,
                                 int(math.floor(0 if n == 0 else math.log10(abs(n)) / 3))))

            return '{:.0f}{}'.format(n / 10**(3 * millidx), millnames[millidx])

        pixels = sql.template_pixels_watched()
        if not pixels:
            pixels = "Pixels!"
        else:
            pixels = f"{millify(pixels)} Pixels!"

        await self.change_presence(
            status=discord.Status.online,
            activity=discord.Activity(
                name=pixels,
                type=discord.ActivityType.watching))

    @set_presense.before_loop
    async def before_status(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def unmute(self):
        sql.mutes_remove_expired()

    @unmute.before_loop
    async def before_unmute(self):
        await self.wait_until_ready()

    async def startup(self):
        await self.wait_until_ready()

        log.info("Starting Starlight Glimmer v{}!".format(VERSION))

        log.info("Performing database check...")
        is_new_version = await self.database_check()
        log.info("Performing guilds check...")
        await self.guilds_check(is_new_version)

        log.info('I am ready!')
        await utils.channel_log(bot, "I am ready!")
        print("I am ready!")

    async def database_check(self):
        is_new_version = False
        if sql.version_get() is None:
            sql.version_init(VERSION)
            return is_new_version

        old_version = sql.version_get()
        is_new_version = old_version != VERSION and old_version is not None

        if not is_new_version:
            return is_new_version

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

        return is_new_version

    async def guilds_check(self, is_new_version):
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
                await utils.print_welcome_message(g)

        db_guilds = sql.guild_get_all()
        if len(bot.guilds) != len(db_guilds):
            for g in db_guilds:
                if not any(x for x in bot.guilds if x.id == g.id):
                    log.info("Kicked from guild '{0}' (ID: {1}) between sessions".format(g.name, g.id))
                    if config.CHANNEL_LOG_GUILD_KICKS:
                        await utils.channel_log(bot, "Kicked from guild **{0}** (ID: `{1}`)".format(g.name, g.id))
                    sql.guild_delete(g.id)

    async def start_checker(self):
        await self.wait_until_ready()
        pcio = checker.Checker(self)
        pcio.connect_websocket()

    async def on_message(self, message):
        # Ignore channels that can't be posted in
        if message.guild and not message.channel.permissions_for(message.guild.me).send_messages:
            return

        # Ignore other bots
        if message.author.bot:
            return

        # Ignore messages from users currently making a menu choice
        locks = utils.sql.menu_locks_get_all()
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
        ctx = await self.get_context(message, cls=GlimContext)
        if ctx.invoked_with:
            await self.invoke(ctx)
            return

        # Autoscan
        await utils.autoscan(ctx)


log = logging.getLogger(__name__)
bot = Glimmer(
    command_prefix=get_prefix,
    case_insensitive=True,
    owner_id=255376766049320960)
sql.menu_locks_delete_all()


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

log.info("Loading cogs...")

for filename in os.listdir('./extensions'):
    filename = filename[:-3] if filename.endswith(".py") else filename
    extensions = ["animotes", "canvas", "configuration", "faction", "general", "template"]
    if filename in extensions:
        bot.load_extension("extensions.{}".format(filename))

bot.run(config.TOKEN)
