import datetime
import logging
import time
import re
from functools import partial
from itertools import groupby

from aiohttp.client_exceptions import ServerDisconnectedError, ClientOSError, ClientConnectorError
import discord
from discord.ext import commands, menus, tasks
import numpy as np

from objects.database_models import \
    (session_scope,
     MutedTemplate,
     Canvas,
     Template as TemplateDb,
     Pixel as PixelDb)
from objects.errors import TemplateNotFoundError, NotEnoughDataError
from utils import canvases, checks, GlimmerArgumentParser, FactionAction, parse_duration, plot, DurationAction
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
                if t.last_alert_message:
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

        except ClientOSError as e:
            log.warning(f"OS error during cleanup, likely broken pipe. {e}")
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

        mute = ctx.session.query(MutedTemplate).filter_by(template_id=template.id).first()
        if mute:
            ctx.session.delete(mute)
            await ctx.send(ctx.s("alerts.unmuted").format(name))

        if channel:
            template.alert_id = channel.id
            await ctx.send(ctx.s("alerts.will_alert").format(name, channel.mention))
        else:
            template.alert_id = None
            await ctx.send(ctx.s("alerts.will_not_alert").format(name))

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='mute', aliases=['m'])
    async def mute(self, ctx, name, duration=None):
        template = ctx.session.query(TemplateDb).filter_by(
            guild_id=ctx.guild.id, name=name).first()

        if not template:
            raise TemplateNotFoundError(ctx, ctx.guild.id, name)

        if not duration:
            if template.alert_id:
                return await ctx.send(ctx.s("alerts.not_muted").format(name))

            mute = ctx.session.query(MutedTemplate).filter_by(template_id=template.id).first()

            if not mute:
                return await ctx.send(ctx.s("alerts.not_muted").format(name))

            template.alert_id = mute.alert_id
            ctx.session.delete(mute)
            await ctx.send(ctx.s("alerts.unmuted").format(name))
        else:
            try:
                duration = float(duration) * 3600
            except ValueError:
                duration = parse_duration(ctx, duration)

            if not template.alert_id:
                return await ctx.send(ctx.s("alerts.already_muted").format(name))

            mute = MutedTemplate(template=template, alert_id=template.alert_id, expires=time.time() + duration)
            ctx.session.add(mute)
            template.alert_id = None
            await ctx.send(ctx.s("alerts.muted").format(name, duration / 3600))

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
            await ctx.send(ctx.s("alerts.no_recent_errors"))
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

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @commands.command(name="alert-stats")
    async def alert_stats(self, ctx, *args):
        try:
            name = args[0]
        except IndexError:
            await ctx.send(ctx.s("error.missing_argument"))
            return

        skip = False
        for arg in args:
            if any(h == arg for h in ["--help", "-h"]):
                args = ["--help"]
                skip = True

        if not skip:
            if re.match(r"-\D+", name) is not None:
                name = args[-1]
                args = args[:-1]
            else:
                args = args[1:]

        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        parser.add_argument("-d", "--duration", default=DurationAction.get_duration(ctx, "1d"), action=DurationAction)
        parser.add_argument("-t", "--type", default="comparision", choices=["comparision", "gain"])
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        log.debug(f"[uuid:{ctx.uuid}] Parsed arguments: {args}")

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        template = ctx.session.query(TemplateDb)\
            .filter_by(guild_id=gid, name=name).first()
        if not template:
            raise TemplateNotFoundError(ctx, gid, name)

        alert_template = None
        for t in self.templates:
            if t.id == template.id:
                alert_template = t
                break

        if not alert_template:
            await ctx.send("Error fetching data, is that template an alert template? (See `{0}help alert` for more info).".format(ctx.prefix))
            return

        start = args.duration.start
        end = args.duration.end

        sq = ctx.session.query(Canvas.id).filter_by(nick=template.canvas)
        q = ctx.session.query(PixelDb).filter(
            PixelDb.placed.between(start, end),
            PixelDb.canvas_id.in_(sq),
            PixelDb.x.between(template.x, template.x + template.width - 1),
            PixelDb.y.between(template.y, template.y + template.height - 1))
        q = q.order_by(PixelDb.placed)
        pixels = q.all()

        if not len(pixels):
            raise NotEnoughDataError

        async with ctx.typing():
            process_func = partial(
                self.process,
                pixels,
                alert_template,
                args.duration.days)
            x_data, y_data = await self.bot.loop.run_in_executor(None, process_func)

            plot_types = {
                "comparision": plot.alert_comparision,
                "gain": plot.alert_gain
            }

            plot_func = partial(
                plot_types.get(args.type),
                ctx,
                x_data,
                y_data[:, 0],
                y_data[:, 1],
                args.duration)
            image = await self.bot.loop.run_in_executor(None, plot_func)

            if args.type == "comparision":
                out = ctx.s("alerts.comparision_title").format(template.name, args.duration.days)
            elif args.type == "gain":
                out = ctx.s("alerts.gain_title").format(template.name, args.duration.days)

            await ctx.send(out, file=discord.File(image, "stats.png"))

    def process(self, pixels, alert_template, days):
        # We need to begin a new session for this thread and migrate all
        # the pixel objects over to it so we can use them safely!
        with session_scope() as session:
            ps = [session.merge(p) for p in pixels]

            x_data = []
            # Uses up wayyy more space than it's gonna need, maybe I should aim low and expand?
            y_data = np.zeros((len(ps), 2), dtype=np.int16)
            for i, (_, pixels) in enumerate(groupby(ps, key=lambda p: f"{p.placed.day} {p.placed.hour}")):
                counter = {"ally": 0, "enemy": 0}
                for pixel in pixels:
                    color = alert_template.color_at(pixel.x, pixel.y)
                    if color == -1:
                        continue
                    elif color == pixel.color:
                        counter["ally"] += 1
                    else:
                        counter["enemy"] += 1

                x_data.append(datetime.datetime(
                    pixel.placed.year, pixel.placed.month,
                    pixel.placed.day, hour=pixel.placed.hour))
                y_data[i, 0] = counter["ally"]
                y_data[i, 1] = counter["enemy"]

            y_data = y_data[0:i + 1, :]
            return x_data, y_data

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
                title=template.s(self.bot, "alerts.alert_title").format(template.name),
                description=template.s(self.bot, "alerts.alert_description"))
            embed.set_thumbnail(url=template.url)
            text = ""

            url = canvases.url_templates[template.canvas]

            for p in template.pixels:
                if (template.last_alert_message and p.alert_id == template.last_alert_message.id) or \
                   (p.alert_id == "flag" and template.id == p.template_id):

                    out = "~~{0}~~\n" if p.fixed else "{0}\n"
                    text += out.format(
                        template.s(self.bot, "bot.pixel").format(
                            x=p.x, y=p.y, url=url.format(p.x, p.y),
                            current=template.color_string(self.bot, p.damage_color),
                            target=template.color_string(self.bot, template.color_at(p.x, p.y))))

            embed.add_field(name=template.s(self.bot, "alerts.received"), value=text, inline=False)
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

            try:
                if template.last_alert_message:
                    await template.last_alert_message.edit(embed=embed)
                else:
                    channel = self.bot.get_channel(template.alert_channel)
                    template.last_alert_message = await channel.send(embed=embed)
                    for p in template.pixels:
                        if p.alert_id == "flag":
                            p.alert_id = template.last_alert_message.id
            except discord.errors.HTTPException:
                log.warning(f"HTTP Exception sending message for {template}")
            except ServerDisconnectedError:
                log.warning(f"Server disconnected while sending message for {template}")
            except ClientOSError:
                log.warning(f"ClientOSError while sending message for {template}")

            # Release send lock
            template.sending = False
        except Exception as e:
            template.sending = False

            if isinstance(e, ClientConnectorError):
                log.warning("Discord connection error while sending/editing an embed.")
            else:
                log.exception("Error sending/editing an embed.")
