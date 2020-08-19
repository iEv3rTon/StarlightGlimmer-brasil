import logging
import math
import time
import os
import re
import traceback

import discord
from discord.ext import commands, tasks
from sqlalchemy.sql import func

from objects.bot_objects import GlimContext
from objects.database_models import session_scope, Guild, MenuLock, MutedTemplate, Template, Version
import utils
from utils import config, http, render
from utils.version import VERSION


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
        except Exception as e:
            log.exception(e)
            await utils.channel_log(self, ''.join(traceback.format_exception(None, e, e.__traceback__)))

    @tasks.loop(minutes=5.0)
    async def unmute(self):
        try:
            with session_scope() as session:
                mutes = session.query(MutedTemplate).filter(
                    MutedTemplate.expires < time.time()).all()

                for mute in mutes:
                    mute.template.alert_channel = mute.alert_id
                    session.delete(mute)

        except Exception as e:
            log.exception(e)
            await utils.channel_log(self, ''.join(traceback.format_exception(None, e, e.__traceback__)))

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

# Delete all old menu locks
with session_scope() as session:
    session.query(MenuLock).delete()


@bot.before_invoke
async def on_command_preprocess(ctx):
    # Logging info
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


bot.run(config.TOKEN)
