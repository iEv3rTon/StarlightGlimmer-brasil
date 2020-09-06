import logging

import discord
from discord.ext import commands, menus
from fuzzywuzzy import fuzz

from objects import errors
import utils
from objects.database_models import Guild, session_scope, Template, MutedTemplate

log = logging.getLogger(__name__)


class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_err_img(self, ctx, image, error_txt):
        try:
            f = discord.File(f"assets/{image}", image)
            await ctx.send(error_txt, file=f)
        except IOError:
            await ctx.send(error_txt)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # Check for original exceptions raised and sent to CommandInvokeError.
        # If nothing is found. We keep the exception passed to on_command_error.
        error = getattr(error, 'original', error)

        # See if error has been handled in a cog local error handler already
        if getattr(error, "handled", None):
            return

        # Command errors
        if isinstance(error, commands.BadArgument):
            pass
        elif isinstance(error, discord.HTTPException) and error.original.code == 50013:
            pass
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(ctx.s("error.cooldown").format(error.retry_after))
        elif isinstance(error, commands.CommandNotFound):
            cmds = []
            for key, command in self.bot.all_commands.items():
                ratio = fuzz.partial_ratio(key, ctx.invoked_with)
                if command not in cmds:
                    command.ratios = [ratio]
                    cmds.append(command)
                else:
                    command.ratios.append(ratio)

            matches = []
            for command in cmds:
                command.ratio = sum(command.ratios) / len(command.ratios)
                if command.ratio > 65:
                    matches.append(command)

            matches.sort(key=lambda match: match.ratio)
            matches = [f"`{cmd.name}`" for cmd in matches[0:5]]

            out = ctx.s("error.command_not_found").format(ctx.invoked_with)
            if matches:
                m = ctx.s("bot.or").format(", ".join(matches[:-1]), matches[-1]) if len(matches) > 1 else matches[0]
                out = "{} {}".format(out, ctx.s("error.did_you_mean").format(m))
            await ctx.send(out)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(ctx.s("error.missing_argument"))
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.send(ctx.s("error.no_dm"))
        elif isinstance(error, commands.MaxConcurrencyReached):
            await ctx.send(ctx.s("error.max_concurrency"))
        elif isinstance(error, commands.CheckFailure):
            await ctx.send(ctx.s("error.no_user_permission"))

        # Menu errors
        elif isinstance(error, menus.CannotAddReactions):
            await ctx.send(error)
        elif isinstance(error, menus.CannotReadMessageHistory):
            await ctx.send(error)

        # Check errors
        elif isinstance(error, errors.BadArgumentErrorWithMessage):
            await ctx.send(error.message)
        elif isinstance(error, errors.FactionNotFoundError):
            await ctx.send(ctx.s("error.faction_not_found"))
        elif isinstance(error, errors.IdempotentActionError):
            await self.send_err_img(ctx, "y_tho.png", ctx.s("error.why"))
        elif isinstance(error, errors.NoAttachmentError):
            await ctx.send(ctx.s("error.no_attachment"))
        elif isinstance(error, errors.NoJpegsError):
            await self.send_err_img(ctx, "disdain_for_jpegs.gif", ctx.s("error.jpeg"))        
        elif isinstance(error, errors.NoSelfPermissionError):
            await ctx.send(ctx.s("error.no_self_permission"))
        elif isinstance(error, errors.NoTemplatesError):
            if error.is_canvas_specific:
                await ctx.send(ctx.s("error.no_templates_for_canvas"))
            else:
                await ctx.send(ctx.s("error.no_templates"))
        elif isinstance(error, errors.NoUserPermissionError):
            await ctx.send(ctx.s("error.no_user_permission"))
        elif isinstance(error, errors.NotPngError):
            await ctx.send(ctx.s("error.not_png"))
        elif isinstance(error, errors.PilImageError):
            await ctx.send(ctx.s("error.bad_image"))
        elif isinstance(error, errors.TemplateHttpError):
            await ctx.send(ctx.s("error.cannot_fetch_template").format(error.template_name))
        elif isinstance(error, errors.TemplateNotFoundError):
            out = ctx.s("error.template_not_found").format(error.query)
            if error.matches != []:
                m = ctx.s("bot.or").format(", ".join(error.matches[:-1]), error.matches[-1]) if len(error.matches) > 1 else error.matches[0]
                out = "{} {}".format(out, ctx.s("error.did_you_mean").format(m))
            await ctx.send(out)
        elif isinstance(error, errors.UrlError):
            await ctx.send(ctx.s("error.non_discord_url"))
        elif isinstance(error, errors.HttpCanvasError):
            await ctx.send(ctx.s("error.http_canvas").format(utils.canvases.pretty_print[error.canvas]))
        elif isinstance(error, errors.HttpGeneralError):
            await ctx.send(ctx.s("error.http"))
        elif isinstance(error, errors.ColorError):
            await ctx.send(ctx.s("error.invalid_color"))
        elif isinstance(error, errors.TemplateTooLargeError):
            await ctx.send(ctx.s("canvas.dither_toolarge").format(error.limit))
        elif isinstance(error, errors.CanvasNotSupportedError):
            await ctx.send(ctx.s("error.canvas_not_supported"))

        # Uncaught error
        else:
            name = ctx.command.qualified_name if ctx.command else "None"
            log.exception("An error occured executing '{0}' in server {1.name} (GID: {1.id})".format(
                name, ctx.guild), exc_info=error)
            await ctx.send(ctx.s("error.unknown").format(ctx.uuid))

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        log.info("Joined new guild '{0.name}' (ID: {0.id})".format(guild))
        if utils.config.CHANNEL_LOG_GUILD_JOINS:
            await utils.channel_log(self.bot, "Joined new guild **{0.name}** (ID: `{0.id}`)".format(guild))

        with session_scope() as session:
            db_guild = Guild(id=guild.id, name=guild.name, join_date=int(guild.me.joined_at.timestamp()))
            session.add(db_guild)

        # await utils.print_welcome_message(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        log.info("Kicked from guild '{0.name}' (ID: {0.id})".format(guild))
        if utils.config.CHANNEL_LOG_GUILD_KICKS:
            await utils.channel_log(self.bot, "Kicked from guild **{0.name}** (ID: `{0.id}`)".format(guild))

        with session_scope() as session:
            db_guild = session.query(Guild).get(guild.id)
            session.delete(db_guild)

    @commands.Cog.listener()
    async def on_guild_update(self, before, after):
        if before.name != after.name:
            log.info("Guild {0.name} is now known as {1.name} (ID: {1.id})")
            if utils.config.CHANNEL_LOG_GUILD_RENAMES:
                await utils.channel_log(self.bot, "Guild **{0.name}** is now known as **{1.name}** (ID: `{1.id}`)".format(before, after))

            with session_scope() as session:
                db_guild = session.query(Guild).get(after.id)
                db_guild.name = after.name

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        with session_scope() as session:
            guild = session.query(Guild).get(role.guild.id)
            if guild.template_admin == role.id:
                guild.template_admin = None
            if guild.template_adder == role.id:
                guild.template_adder = None
            if guild.bot_admin == role.id:
                guild.bot_admin = None

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        with session_scope() as session:
            guild = session.query(Guild).get(channel.guild.id)

            if guild.alert_channel == channel.id:
                guild.alert_channel = None

            session.query(Template).filter_by(
                guild_id=channel.guild.id, alert_id=channel.id).update(
                    {Template.alert_id: None})

            session.query(MutedTemplate).filter(
                MutedTemplate.template.guild_id == channel.guild.id,
                MutedTemplate.alert_id == channel.id).delete()
