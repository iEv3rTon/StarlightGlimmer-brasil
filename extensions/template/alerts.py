import datetime
import logging
import time
import re

import discord
from discord.ext import commands, menus, tasks

from objects.database_models import session_scope, Template as TemplateDb, MutedTemplate
from objects.errors import TemplateNotFoundError, CanvasNotSupportedError
from utils import canvases, checks, GlimmerArgumentParser, FactionAction
from extensions.template.utils import CheckerSource, Pixel, Template

log = logging.getLogger(__name__)


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.templates = []
        self.subscriptions = []

        self.bot.loop.create_task(self.start_checker())

    def cog_unload(self):
        self.subscribe.cancel()
        self.update.cancel()
        self.cleanup.cancel()

        self.bot.loop.create_task(
            self.bot.unsubscribe_canvas_listeners(
                self.subscriptions))

    async def start_checker(self):
        await self.bot.wait_until_ready()
        log.info("Starting template checker...")
        self.subscribe.start()
        self.update.start()
        self.cleanup.start()

    @tasks.loop(seconds=30.0)
    async def subscribe(self):
        subs = [sub["canvas"] for sub in self.subscriptions]
        new_subs = set()

        for template in self.templates:
            if template.canvas not in subs:
                new_subs.add(template.canvas)

        for canvas in new_subs:
            sub_func = self.bot.subscribers[canvas]
            s_uuid = await sub_func(self.on_message)
            self.subscriptions.append({"canvas": canvas, "uuid": s_uuid})

    @tasks.loop(minutes=5.0)
    async def update(self):
        try:
            with session_scope() as session:
                # Get the templates currently in the db
                templates = session.query(TemplateDb).filter(
                    TemplateDb.alert_id != None).all()

                # Remove any templates from self.templates that are no longer in db
                db_t_ids = [t.id for t in templates]
                for t in self.templates:
                    if t.id not in db_t_ids:
                        log.debug(f"Template {t} no longer in the database, removed.")
                        self.templates.remove(t)

                # Update any templates already in self.templates that have changed
                for old_t in self.templates:
                    for t in templates:
                        if old_t.id != t.id:
                            continue
                        if not old_t.changed(t):
                            continue

                        tp = await Template.new(t)
                        if tp is not None:
                            self.templates.remove(old_t)
                            self.templates.append(tp)

                # Add any new templates from db to self.templates
                template_ids = [template.id for template in self.templates]
                for t in templates:
                    if t.id in template_ids:
                        continue
                    tp = await Template.new(t)
                    if tp is None:
                        continue

                    self.templates.append(tp)

        except Exception as e:
            log.exception(f'Failed to update. {e}')

    @tasks.loop(minutes=1.0)
    async def cleanup(self):
        try:
            # Clean up old alert messages
            channel_messages = {}
            for t in self.templates:
                if not t.last_alert_message:
                    continue

                # Get last 5 messages in channel
                messages = channel_messages.get(t.alert_channel)
                if not messages:
                    alert_channel = self.bot.get_channel(t.alert_channel)
                    messages = await alert_channel.history(limit=5).flatten()
                    channel_messages[t.alert_channel] = messages

                # Clear the alert message if it isn't recent anymore so new alerts will be at the bottom of the channel
                if not any(m.id == t.last_alert_message.id for m in messages):
                    log.debug(f"Alert message for {t} is more than 5 messages ago, clearing.")
                    t.last_alert_message = None

            # Clean up old pixels
            _5_mins = 60 * 30
            now = time.time()
            for t in self.templates:
                for p in t.pixels:
                    # Pixels recieved more than 5 mins ago that are not attached to the current alert msg will be cleared
                    if not t.last_alert_message:
                        if (now - p.recieved) > _5_mins and p.alert_id != "flag":
                            log.debug(f"Clearing {p}.")
                            t.pixels.remove(p)
                    elif (now - p.recieved) > _5_mins and p.alert_id != t.last_alert_message.id:
                        log.debug(f"Clearing {p}.")
                        t.pixels.remove(p)

        except Exception as e:
            log.exception(f'Cleanup failed. {e}')

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='alert')
    async def alert(self, ctx, name, channel: discord.TextChannel = None):
        template = ctx.session.query(TemplateDb).filter_by(
            guild_id=ctx.guild.id, name=name).first()

        if not template:
            raise TemplateNotFoundError(ctx, ctx.guild.id, name)

        if template.canvas not in ["pixelcanvas", "pixelzone"]:
            raise CanvasNotSupportedError()

        mute = ctx.session.query(MutedTemplate).filter_by(template_id=template.id).first()
        if mute:
            ctx.session.delete(mute)
            await ctx.send(ctx.s("template.unmuted").format(name))

        if channel:
            template.alert_id = channel.id
            await ctx.send(ctx.s("template.will_alert").format(name, channel.mention))
        else:
            template.alert_id = None
            await ctx.send(ctx.s("template.will_not_alert").format(name))

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='mute', aliases=['m'])
    async def mute(self, ctx, name, duration=None):
        template = ctx.session.query(TemplateDb).filter_by(
            guild_id=ctx.guild.id, name=name).first()

        if not template:
            raise TemplateNotFoundError(ctx, ctx.guild.id, name)

        if template.canvas not in ["pixelcanvas", "pixelzone"]:
            raise CanvasNotSupportedError()

        if not duration:
            if template.alert_id:
                return await ctx.send(ctx.s("template.not_muted").format(name))

            mute = ctx.session.query(MutedTemplate).filter_by(template_id=template.id).first()

            if not mute:
                return await ctx.send(ctx.s("template.not_muted").format(name))

            template.alert_id = mute.alert_id
            ctx.session.delete(mute)
            await ctx.send(ctx.s("template.unmuted").format(name))
        else:
            try:
                duration = float(duration) * 3600
            except ValueError:
                matches = re.findall(r"(\d+[wdhms])", duration.lower())  # Week Day Hour Minute Second

                if not matches:
                    return await ctx.send(ctx.s("template.invalid_duration_1"))

                suffixes = [match[-1] for match in matches]
                if len(suffixes) != len(set(suffixes)):
                    return await ctx.send(ctx.s("template.invalid_duration_2"))

                seconds = {
                    "w": 7 * 24 * 60 * 60,
                    "d": 24 * 60 * 60,
                    "h": 60 * 60,
                    "m": 60,
                    "s": 1
                }

                duration = sum([int(match[:-1]) * seconds.get(match[-1]) for match in matches])

            if not template.alert_id:
                return await ctx.send(ctx.s("template.already_muted").format(name))

            mute = MutedTemplate(template=template, alert_id=template.alert_id, expires=time.time() + duration)
            ctx.session.add(mute)
            template.alert_id = None
            await ctx.send(ctx.s("template.muted").format(name, duration / 3600))

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

        log.debug(f"[uuid:{ctx.uuid}] Parsed arguments: {args}")

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        templates = ctx.session.query(TemplateDb).filter_by(guild_id=gid).all()
        checker_templates = [t for t in self.templates if t.id in [t_.id for t_ in templates]]
        pixels = [p for t in checker_templates for p in t.current_pixels if p.fixed is False]
        pixels.sort(key=lambda p: p.recieved, reverse=True)

        if not pixels:
            await ctx.send(ctx.s("template.no_recent_errors"))
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

    async def on_message(self, x, y, color, canvas):
        for template in self.templates:
            if template.canvas != canvas:
                continue
            if not template.in_range(x, y):
                continue

            self.bot.loop.create_task(self.check_template(template, x, y, color))

    async def check_template(self, template, x, y, color):
        template_color = template.color_at(x, y)
        if color == template_color:
            # Is this pixel in the most recent alert?
            for p in template.pixels:
                if p.x == x and p.y == y:
                    p.fixed = True
                    log.debug(f"Tracked pixel {p.x},{p.y} fixed to {Pixel.colors[template.canvas][color]}, was {p.log_color}. On {template}")
                    await self.send_embed(template)
        elif color != template_color and template_color > -1:
            if not template.last_alert_message:
                # No current alert message, make a new one
                p = template.add_pixel(color, x, y)
                log.debug(f"Untracked pixel {p.x},{p.y} damaged to {p.log_color}, new message created. On {template}")
                await self.send_embed(template)
                return

            # If pixel is already being tracked, just update the data
            for p in template.current_pixels:
                if p.x == x and p.y == y:
                    p.damage_color = color
                    p.fixed = False
                    log.debug(f"Tracked pixel {p.x},{p.y} damaged to {p.log_color}. On {template}")
                    await self.send_embed(template)
                    return

            # Pixel is not already tracked, add it to an old message
            if len(template.current_pixels) < 10:
                p = template.add_pixel(color, x, y)
                log.debug(f"Untracked pixel {p.x},{p.y} damaged to {p.log_color}. On {template}")
                await self.send_embed(template)

                # If adding our pixel increases it to 10, a new message needs to be created
                if len(template.current_pixels) == 10:
                    log.debug(f"Clearing alert message for {template}")
                    template.last_alert_message = None

    async def send_embed(self, template):
        try:
            if template.sending:
                return
            template.sending = True

            embed = discord.Embed(
                title=template.s("alerts.alert_title").format(template.name),
                description=template.s("alerts.alert_description"))
            embed.set_thumbnail(url=template.url)
            text = ""

            url = canvases.url_templates[template.canvas]

            for p in template.pixels:
                if (template.last_alert_message and p.alert_id == template.last_alert_message.id) or \
                   (p.alert_id == "flag" and template.id == p.template_id):

                    out = "~~{0}~~\n" if p.fixed else "{0}\n"
                    text += out.format(
                        template.s("bot.pixel").format(
                            x=p.x, y=p.y, url=url.format(p.x, p.y),
                            current=template.color_string(p.damage_color),
                            target=template.color_string(template.color_at(p.x, p.y))))

            embed.add_field(name=template.s("alerts.recieved"), value=text, inline=False)
            embed.timestamp = datetime.datetime.now()

            try:
                if template.last_alert_message:
                    await template.last_alert_message.edit(embed=embed)
                else:
                    channel = self.bot.get_channel(template.alert_channel)
                    template.last_alert_message = await channel.send(embed=embed)
                    for p in template.pixels:
                        if p.alert_id == "flag":
                            p.alert_id = template.last_alert_message.id
            except discord.errors.HTTPException as e:
                log.debug(f"Exception sending message for {template}")

            # Release send lock
            template.sending = False
        except Exception as e:
            template.sending = False
            log.exception("Error sending/editing an embed.")
