import datetime
import logging
import time
import re
from functools import partial
from io import BytesIO

import discord
from discord.ext import commands, menus, tasks
import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from objects.database_models import session_scope, Template as TemplateDb, MutedTemplate
from objects.errors import TemplateNotFoundError
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
                matches = re.findall(r"(\d+[wdhms])", duration.lower())  # Week Day Hour Minute Second

                if not matches:
                    return await ctx.send(ctx.s("alerts.invalid_duration_1"))

                suffixes = [match[-1] for match in matches]
                if len(suffixes) != len(set(suffixes)):
                    return await ctx.send(ctx.s("alerts.invalid_duration_2"))

                seconds = {
                    "w": 7 * 24 * 60 * 60,
                    "d": 24 * 60 * 60,
                    "h": 60 * 60,
                    "m": 60,
                    "s": 1
                }

                duration = sum([int(match[:-1]) * seconds.get(match[-1]) for match in matches])

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

        if re.match(r"-\D+", name) is not None:
            name = args[-1]
            args = args[:-1]
        else:
            args = args[1:]

        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        parser.add_argument("-d", "--days", type=int, default=1, choices=range(1, 8))
        parser.add_argument("-t", "--type", default="comparision", choices=["comparision", "gain"])
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        log.debug(f"[uuid:{ctx.uuid}] Parsed arguments: {args}")

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        template = ctx.session.query(TemplateDb).filter_by(
            guild_id=gid, name=name).first()
        if not template:
            raise TemplateNotFoundError(ctx, gid, name)

        if not template.alert_stats:
            await ctx.send(ctx.s("alerts.no_stats"))
            return

        async with ctx.typing():
            func = partial(
                self.plot,
                ctx,
                template.alert_stats,
                args.days,
                args.type)
            image = await self.bot.loop.run_in_executor(None, func)

            if args.type == "comparision":
                out = ctx.s("alerts.comparision_title").format(template.name, args.days)
            elif args.type == "gain":
                out = ctx.s("alerts.gain_title").format(template.name, args.days)

            await ctx.send(out, file=discord.File(image, "stats.png"))

    def plot(self, ctx, data, days, type):
        now = datetime.datetime.now(datetime.timezone.utc)

        x_values = []
        ally_values = []
        enemy_values = []
        gain_values = []
        gain_colors = []
        datemin = None
        datemax = None

        deltas = [x for x in range(days + 1)]
        deltas.reverse()
        for i, x in enumerate(deltas):
            time = now - datetime.timedelta(days=x)

            if i == 0:
                datemin = time
            datemax = time

            for hour in range(24):
                time_hour = datetime.datetime(time.year, time.month, time.day, hour=hour, tzinfo=datetime.timezone.utc)

                try:
                    pixels = data["{0.day}/{0.month}/{0.year}".format(time_hour)][str(time_hour.hour)]
                except KeyError:
                    continue

                x_values.append(time_hour)

                if type == "comparision":
                    ally_values.append(pixels["ally"])
                    enemy_values.append(pixels["enemy"])
                elif type == "gain":
                    gain = pixels["ally"] - pixels["enemy"]
                    gain_values.append(gain)
                    gain_colors.append("r" if gain < 0 else "g")

        d = matplotlib.dates.DayLocator()
        h = matplotlib.dates.HourLocator(byhour=[6, 12, 18])
        day_formatter = matplotlib.dates.DateFormatter("%d/%m/%Y" if days <= 4 else "%d/%m/%y")
        hour_formatter = matplotlib.dates.DateFormatter("%I%p")

        def format_date(ax):
            if days <= 4:
                ax.xaxis.set_minor_formatter(hour_formatter)
                ax.xaxis.set_tick_params(which='minor', labelsize=7.0)

            ax.xaxis.set_minor_locator(h)
            ax.xaxis.set_major_locator(d)
            ax.xaxis.set_major_formatter(day_formatter)
            ax.xaxis.set_tick_params(which='major', pad=15, labelsize=9.0)
            ax.set_xlim(datemin, datemax)

        # Creating the figure this way because plt isn't garbage collected (It should be, what the fuck matplotlib)
        fig = Figure()
        _ = FigureCanvasAgg(fig)  # Strange API, this is binding the figure to a canvas so stuff can get drawn

        if type == "comparision":
            ax_1, ax_2 = fig.subplots(2, sharex=True, sharey=True)

            ax_1.plot(x_values, ally_values, color="green")
            ax_2.plot(x_values, enemy_values, color="red")
            ax_1.grid(True)
            ax_2.grid(True)
            ax_1.set_title(ctx.s("alerts.allies"))
            ax_2.set_title(ctx.s("alerts.enemies"))
            ax_1.set_ylabel(ctx.s("alerts.comparision_y_label"))
            ax_2.set_ylabel(ctx.s("alerts.comparision_y_label"))
            format_date(ax_2)
            ax_2.set_ylim(bottom=0)
        elif type == "gain":
            ax = fig.subplots()

            ax.scatter(x_values, gain_values, c=gain_colors)
            ax.grid(True)
            ax.set_ylabel(ctx.s("alerts.gain_y_label"))
            format_date(ax)

            # Center y axis at zero
            bottom, top = ax.get_ylim()
            biggest = max(abs(bottom), abs(top))
            ax.set_ylim(bottom=0 - biggest, top=biggest)

        buf = BytesIO()
        fig.savefig(buf, format="png")
        buf.seek(0)
        return buf

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
            self.bot.loop.create_task(self.update_stats(template, True))

            # Is this pixel in the most recent alert?
            for p in template.pixels:
                if p.x == x and p.y == y:
                    p.fixed = True
                    log.debug(f"Tracked pixel {p.x},{p.y} fixed to {Pixel.colors[template.canvas][color]}, was {p.log_color}. On {template}")
                    await self.send_embed(template)
        elif color != template_color and template_color > -1:
            self.bot.loop.create_task(self.update_stats(template, False))

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

            embed.add_field(name=template.s("alerts.received"), value=text, inline=False)
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
            except discord.errors.HTTPException as e:
                log.debug(f"Exception sending message for {template}")

            # Release send lock
            template.sending = False
        except Exception as e:
            template.sending = False
            log.exception("Error sending/editing an embed.")

    async def update_stats(self, template, ally):
        with session_scope() as session:
            db_template = session.query(TemplateDb).get(template.id)
            if not db_template:
                return

            stats = db_template.alert_stats
            stats = stats if stats else {}
            now = datetime.datetime.now(datetime.timezone.utc)

            day = f"{now.day}/{now.month}/{now.year}"
            hour = f"{now.hour}"

            try:
                _ = stats[day]
            except KeyError:
                stats[day] = {}

            try:
                _ = stats[day][hour]
            except KeyError:
                stats[day][hour] = {"ally": 0, "enemy": 0}

            if ally:
                stats[day][hour]["ally"] += 1
            else:
                stats[day][hour]["enemy"] += 1

            # Remove data that's more than a week old
            valid_days = []
            for x in range(7):
                time = now - datetime.timedelta(days=x)
                valid_days.append(f"{time.day}/{time.month}/{time.year}")

            for key in stats.keys():
                if key not in valid_days:
                    del stats[key]

            session.query(TemplateDb).filter_by(id=template.id).update({
                "alert_stats": stats})
