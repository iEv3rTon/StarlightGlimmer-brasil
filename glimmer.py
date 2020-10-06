import logging
import math
import time
import os
import re
import datetime
import functools

import discord
from discord.ext import commands, tasks
from sqlalchemy.sql import func
import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

from objects.bot_objects import GlimContext
from objects.database_models import session_scope, Guild, MenuLock, MutedTemplate, Template, Version, Pixel, Canvas, Online
import utils
from utils import config, http, render, websocket, canvases
from utils.version import VERSION

if config.SENTRY_DSN:
    sentry_logging = LoggingIntegration(
        level=logging.DEBUG,
        event_level=logging.ERROR)
    sentry_sdk.init(
        dsn=config.SENTRY_DSN,
        integrations=[sentry_logging, SqlalchemyIntegration()])


def get_prefix(bot_, msg: discord.Message):
    prefix_list = [config.PREFIX, bot_.user.mention + " "]

    if msg.guild:
        with session_scope() as session:
            guild = session.query(Guild).get(msg.guild.id)
            if guild and guild.prefix is not None:
                prefix_list[0] = guild.prefix

    return prefix_list


class Glimmer(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_time = time.time()
        self.fetchers = {
            'pixelcanvas': render.fetch_pixelcanvas,
            'pixelzone': render.fetch_pixelzone,
            'pxlsspace': render.fetch_pxlsspace
        }

        log.info("Performing canvas check...")
        self.canvas_check()

        self.pc = websocket.PixelCanvasConnection(self)
        self.pz = websocket.PixelZoneConnection(self)
        self.px = websocket.PxlsSpaceConnection(self)

        # Once all the ws connections are instantiated, we can safely subscribe listeners to them
        self.subscribers = {
            "pixelcanvas": self.pc.add_listener,
            "pixelzone": self.pz.add_listener,
            "pxlsspace": self.px.add_listener
        }
        self.unsubscribers = {
            "pixelcanvas": self.pc.remove_listener,
            "pixelzone": self.pz.remove_listener,
            "pxlsspace": self.px.remove_listener
        }

        log.info("Loading cogs...")

        for filename in os.listdir('./extensions'):
            filename = filename[:-3] if filename.endswith(".py") else filename
            extensions = ["animotes", "canvas", "configuration", "faction", "general", "template"]
            if filename in extensions:
                self.load_extension("extensions.{}".format(filename))

        self.loop.create_task(self.startup())

    @tasks.loop(minutes=30.0)
    async def set_presense(self):
        try:
            # https://stackoverflow.com/a/3155023
            def millify(n):
                millnames = ['', 'K', 'M', 'B', 'T']
                n = float(n)
                millidx = max(0, min(len(millnames) - 1,
                                     int(math.floor(0 if n == 0 else math.log10(abs(n)) / 3))))

                return '{:.0f}{}'.format(n / 10**(3 * millidx), millnames[millidx])

            with session_scope() as session:
                pixels = session.query(func.sum(Template.size)).filter(
                    Template.alert_id != None).scalar()

            if not pixels:
                pixels = "Pixels!"
            else:
                pixels = f"{millify(pixels)} Pixels!"

            await self.change_presence(
                status=discord.Status.online,
                activity=discord.Activity(
                    name=pixels,
                    type=discord.ActivityType.watching))
        except Exception:
            log.exception("Error during status update task.")

    @tasks.loop(minutes=5.0)
    async def unmute(self):
        try:
            with session_scope() as session:
                mutes = session.query(MutedTemplate).filter(
                    MutedTemplate.expires < time.time()).all()

                for mute in mutes:
                    mute.template.alert_channel = mute.alert_id
                    session.delete(mute)

        except Exception:
            log.exception("Error during unmute task.")

    @tasks.loop(minutes=30.0)
    async def pc_online_stats(self):
        try:
            count = int(await http.fetch_online_pixelcanvas())
            if not count:
                log.warning("Error fetching pixelcanvas online count for stats.")
                return

            self.pc.update_online(time.time(), count)
        except Exception:
            log.exception("Error during pixelcanvas online stats update task.")

    @tasks.loop(hours=24.0)
    async def clear_old_stats(self):
        try:
            now = datetime.datetime.now(datetime.timezone.utc)
            seven_days_ago = now - datetime.timedelta(days=7)
            with session_scope() as session:
                session.query(Pixel).filter(Pixel.placed < seven_days_ago).delete()
                session.query(Online).filter(Online.time < seven_days_ago).delete()
        except Exception:
            log.exception("Error during old stats clearing task.")

    async def unsubscribe_canvas_listeners(self, subscriptions):
        for sub in subscriptions:
            unsub = self.unsubscribers[sub["canvas"]]
            await unsub(sub["uuid"])

    async def startup(self):
        await self.wait_until_ready()

        log.info("Starting Starlight Glimmer v{}!".format(VERSION))

        log.info("Performing database check...")
        is_new_version = await self.database_check()

        log.info("Performing guilds check...")
        await self.guilds_check(is_new_version)

        log.info("Beginning status and unmute tasks...")
        self.set_presense.start()
        self.unmute.start()

        log.info("Beginning canvas statistics tasks...")
        self.pc_online_stats.start()
        self.clear_old_stats.start()

        log.info("Beginning canvas websocket connections...")
        self.loop.create_task(self.pz.run())
        # self.loop.create_task(self.pc.run())  arkie's ws ssl cert expired lmaoooooo
        self.loop.create_task(self.px.run())

        log.info('I am ready!')
        await utils.channel_log(self, "I am ready!")
        print("I am ready!")

    async def database_check(self):
        with session_scope() as session:
            is_new_version = False

            old_version = session.query(Version).first()
            if not old_version:
                print("version initialized to {}".format(VERSION))
                version = Version(version=VERSION)
                session.add(version)
                return is_new_version

            is_new_version = old_version.version != VERSION

            if not is_new_version:
                return is_new_version

            log.info("Database is a previous version. Updating...")

            print("updated to {}".format(VERSION))
            old_version.version = VERSION

            return is_new_version

    async def guilds_check(self, is_new_version):
        with session_scope() as session:
            for g in bot.guilds:
                log.info("'{0.name}' (ID: {0.id})".format(g))

                db_g = session.query(Guild).get(g.id)
                if db_g:
                    prefix = db_g.prefix if db_g.prefix else config.PREFIX
                    if g.name != db_g.name:
                        if config.CHANNEL_LOG_GUILD_RENAMES:
                            await utils.channel_log(bot, "Guild **{1}** is now known as **{0.name}** `(ID:{0.id})`".format(g, db_g.name))
                        db_g.name = g.name
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
                                await ch.send(GlimContext.get_from_guild(self, g, "bot.update").format(VERSION, prefix), embed=e)
                            else:
                                await ch.send(GlimContext.get_from_guild(self, g, "bot.update_no_changelog").format(VERSION, prefix))
                            log.info("- Sent update message")
                        else:
                            log.info("- Could not send update message: alert channel not found.")
                else:
                    j = g.me.joined_at
                    if config.CHANNEL_LOG_GUILD_JOINS:
                        await utils.channel_log(bot, "Joined guild **{0.name}** (ID: `{0.id}`)".format(g))
                    log.info("Joined guild '{0.name}' (ID: {0.id}) between sessions at {1}".format(g, j.timestamp()))
                    session.add(Guild(id=g.id, name=g.name, join_date=int(j.timestamp())))
                    # await utils.print_welcome_message(g)

            db_guilds = session.query(Guild).all()
            if len(bot.guilds) != len(db_guilds):
                for g in db_guilds:
                    if not any(x for x in bot.guilds if x.id == g.id):
                        log.info("Kicked from guild '{0}' (ID: {1}) between sessions".format(g.name, g.id))
                        if config.CHANNEL_LOG_GUILD_KICKS:
                            await utils.channel_log(bot, "Kicked from guild **{0}** (ID: `{1}`)".format(g.name, g.id))
                        session.delete(g)

    def canvas_check(self):
        with session_scope() as session:
            for nick, url in canvases.pretty_print.items():
                canvas = session.query(Canvas).filter_by(nick=nick).first()
                if canvas:
                    continue

                log.info(f"Canvas {nick} not found in database, creating...")
                session.add(Canvas(nick=nick, url=url))

    @functools.lru_cache(maxsize=128, typed=False)
    def get_guild_language(self, guild):
        with session_scope() as session:
            if isinstance(guild, discord.Guild):
                guild = session.query(Guild).get(guild.id)
            else:
                guild = session.query(Guild).get(guild)

            return guild.language.lower()

    async def on_message(self, message):
        # Ignore channels that can't be posted in
        if message.guild and not message.channel.permissions_for(message.guild.me).send_messages:
            return

        # Ignore other bots
        if message.author.bot:
            return

        # Ignore messages from users currently making a menu choice
        with session_scope() as session:
            lock = session.query(MenuLock).filter(
                message.author.id == MenuLock.user_id,
                message.channel.id == MenuLock.channel_id
            ).first()

            if lock:
                return

        # Ignore messages with spoilered images
        for attachment in message.attachments:
            if attachment.is_spoiler():
                return

        # Ignore messages with any spoilered text
        if re.match(r".*\|\|.*\|\|.*", message.content):
            return

        with session_scope() as session:
            # Create context from message, and assign a db session to it
            # we do that here because before_invoke doesn't happen
            # before checks
            ctx = await self.get_context(message, cls=GlimContext)
            ctx.session = session

            # Attempt to invoke a command from the context
            await self.invoke(ctx)

            # A command was recognised, so autoscan doesn't need to occur
            if ctx.invoked_with:
                return

            # Autoscan, since the message contained no command
            await utils.autoscan(ctx)


log = logging.getLogger(__name__)
bot = Glimmer(
    command_prefix=get_prefix,
    case_insensitive=True,
    owner_id=255376766049320960)


class DiscordLogger(logging.Handler):
    def __init__(self, bot):
        logging.Handler.__init__(self)
        self.bot = bot
        self.disconnected_msg = "Discord websocket connection closed, exception was not sent"

    def emit(self, record):
        if record.message == self.disconnected_msg:
            return  # Don't recurse with this one

        if config.LOGGING_CHANNEL_ID:
            if self.bot.is_closed():
                log.warning(self.disconnected_msg)
                return

            channel = self.bot.get_channel(config.LOGGING_CHANNEL_ID)
            if not channel:
                log.warning("Can't find logging channel")
            else:
                try:
                    message = self.format(record)
                    if len(message) < 1990:
                        self.bot.loop.create_task(channel.send(f"```{message}```"))
                    else:
                        message = f"{message[:990]}\n...\n{message[-990:]}"
                        self.bot.loop.create_task(channel.send(f"```{message}```"))
                except discord.errors.Forbidden:
                    log.warning("Forbidden from logging channel!")


# Use a handler to try to send error level logs to my discord logging channel
global_logger = logging.getLogger()

error_log = DiscordLogger(bot)
error_log.setLevel(logging.ERROR)
error_log.setFormatter(config.formatter)

global_logger.addHandler(error_log)


# Delete all old menu locks
with session_scope() as session:
    session.query(MenuLock).delete()


@bot.before_invoke
async def on_command_preprocess(ctx):
    if ctx.guild:
        log.info("[uuid:{0}] {1.name}#{1.discriminator} used '{2}' in {3.name} (UID:{1.id} GID:{3.id})"
                 .format(ctx.uuid, ctx.author, ctx.command.qualified_name, ctx.guild))
    else:
        log.info("[uuid:{0}] {1.name}#{1.discriminator} used '{2}' in DM (UID:{1.id})"
                 .format(ctx.uuid, ctx.author, ctx.command.qualified_name))
    log.info("[uuid:{0}] {1}".format(ctx.uuid, ctx.message.content))


bot.run(config.TOKEN)
