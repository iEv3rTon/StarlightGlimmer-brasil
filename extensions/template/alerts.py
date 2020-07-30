import asyncio
import datetime
import logging
import math
import time
import re
import uuid
from io import BytesIO
import socket
from struct import unpack_from

import aiohttp
import discord
from discord.ext import commands, menus, tasks
from PIL import Image
import websockets

from objects.errors import TemplateNotFoundError, CanvasNotSupportedError
from utils import checks, converter, GlimmerArgumentParser, FactionAction, sqlite as sql
from extensions.template.utils import CheckerSource, Pixel, Template

log = logging.getLogger(__name__)


class Alerts(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.templates = []
        self.fingerprint = self.make_fingerprint()

        self.bot.loop.create_task(self.start_checker())

    def cog_unload(self):
        self.checker_update.cancel()
        self.websocket_task.cancel()

    async def start_checker(self):
        await self.bot.wait_until_ready()
        log.info("Starting template checker...")
        self.checker_update.start()
        self.websocket_task = self.bot.loop.create_task(self.run_websocket())

    @tasks.loop(minutes=5.0)
    async def checker_update(self):
        await self.update()

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='alert')
    async def alert(self, ctx, name, channel: discord.TextChannel = None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        if template.canvas != "pixelcanvas":
            raise CanvasNotSupportedError()

        mute = sql.mute_get(template.id)
        if mute:
            sql.mute_remove(template.id)
            alert_id, _, _ = mute
            mute_channel = self.bot.get_channel(alert_id)
            await ctx.send(f"Mute for `{name}` in {mute_channel.mention} cleared.")

        if channel:
            sql.template_kwarg_update(ctx.guild.id, name, alert_id=channel.id)
            await ctx.send(f"`{name}` will now alert in the channel {channel.mention} when damaged.")
        else:
            sql.template_remove_alert(template.id)
            await ctx.send(f"`{name}` will no longer alert for damage.")

    @commands.guild_only()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.command(name='mute', aliases=['m'])
    async def mute(self, ctx, name, duration=None):
        template = sql.template_get_by_name(ctx.guild.id, name)

        if not template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        if template.canvas != "pixelcanvas":
            raise CanvasNotSupportedError()

        if not duration:
            if template.alert_id:
                return await ctx.send(f"`{name}` is not currently muted.")

            sql.mute_remove(template.id)
            await ctx.send(f"Unmuted `{name}`.")
        else:
            try:
                duration = float(duration) * 3600
            except ValueError:
                matches = re.findall(r"(\d+[wdhms])", duration.lower())  # Week Day Hour Minute Second

                if not matches:
                    return await ctx.send("Invalid mute duration, give the number of hours or format like `1h8m`")

                suffixes = [match[-1] for match in matches]
                if len(suffixes) != len(set(suffixes)):
                    return await ctx.send("Invalid mute duration, duplicate time suffix (eg: 1**h**8m3**h**)")

                seconds = {
                    "w": 7 * 24 * 60 * 60,
                    "d": 24 * 60 * 60,
                    "h": 60 * 60,
                    "m": 60,
                    "s": 1
                }

                duration = sum([int(match[:-1]) * seconds.get(match[-1]) for match in matches])

            if not template.alert_id:
                return await ctx.send(f"`{name}` has no alert channel/is already muted.")

            sql.mute_add(ctx.guild.id, template, time.time() + duration)
            await ctx.send(f"`{name}` muted for {duration / 3600:.2f} hours.")

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

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        templates = sql.template_get_all_by_guild_id(gid)
        checker_templates = [t for t in self.templates if t.id in [t_.id for t_ in templates]]
        pixels = [p for t in checker_templates for p in t.pixels if p.fixed is False]
        pixels.sort(key=lambda p: p.recieved, reverse=True)

        if not pixels:
            await ctx.send("No recent errors found.")
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

    def make_fingerprint(self):
        return uuid.uuid4().hex

    def template_changed(self, t_db, t_list):
        return (t_db.x != t_list.sx) or \
               (t_db.y != t_list.sy) or \
               (t_db.md5 != t_list.md5) or \
               (t_db.name != t_list.name) or \
               (t_db.alert_id != t_list.alert_channel)

    async def generate_template(self, t):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(t.url) as resp:
                    if resp.status == 200:
                        image = Image.open(BytesIO(await resp.read())).convert('RGBA')
                    else:
                        # Skip this template, it can get updated on the next round, no point retrying and delaying the others
                        log.exception(f"File for {t.name} could not be downloaded, status code: {resp.status}")
                        return None

            template = Template(t.id, t.name, converter.image_to_array(image), t.url, t.md5,
                                t.x, t.y, t.alert_id, t.gid)
            log.debug(f"Generated {template}.")
            return template
        except Exception as e:
            log.exception(f'Failed to generate {t}. {e}')
            return None

    async def update(self):
        try:
            # Get the templates currently in the db
            templates = sql.template_get_all_alert()

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
                    if not self.template_changed(t, old_t):
                        continue

                    tp = await self.generate_template(t)
                    if tp is not None:
                        self.templates.remove(old_t)
                        self.templates.append(tp)

            # Add any new templates from db to self.templates
            template_ids = [template.id for template in self.templates]
            for t in templates:
                if t.id in template_ids:
                    continue
                tp = await self.generate_template(t)
                if tp is None:
                    continue

                self.templates.append(tp)

            # Do cleanup on data
            _5_mins = 60 * 30
            now = time.time()
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

                # Clean up old pixel data
                for p in t.pixels:
                    # Pixels recieved more than 5 mins ago that are not attached to the current alert msg will be cleared
                    if not t.last_alert_message and (now - p.recieved) > _5_mins and p.alert_id != "flag":
                        log.debug(f"Clearing {p}.")
                        t.pixels.remove(p)
                    elif (now - p.recieved) > _5_mins and p.alert_id != t.last_alert_message.id:
                        log.debug(f"Clearing {p}.")
                        t.pixels.remove(p)

        except Exception as e:
            log.exception(f'Failed to update. {e}')

    async def run_websocket(self):
        while True:
            log.debug("Connecting to websocket...")
            try:
                url = f"wss://ws.pixelcanvas.io:8443/?fingerprint={self.fingerprint}"
                async with websockets.connect(url) as ws:
                    async for message in ws:
                        await self.on_message(message)
            except websockets.exceptions.ConnectionClosed:
                log.debug("Websocket disconnected.")
            except socket.gaierror:
                log.debug("Temporary failure in name resolution.")
            except Exception as e:
                log.exception(f"Error launching! {e}")

            await asyncio.sleep(0.5)

    async def on_message(self, message):
        try:
            if unpack_from('B', message, 0)[0] == 193:
                x = unpack_from('!h', message, 1)[0]
                y = unpack_from('!h', message, 3)[0]
                a = unpack_from('!H', message, 5)[0]
                number = (65520 & a) >> 4
                x = int(x * 64 + ((number % 64 + 64) % 64))
                y = int(y * 64 + math.floor(number / 64))
                color = 15 & a

                # log.debug("Pixel placed, ({0},{1}) colour:{2}".format(x, y, colors[color]))

                for template in self.templates:
                    if not template.in_range(x, y):
                        continue
                    self.bot.loop.create_task(self.check_template(template, x, y, color))
        except Exception as e:
            log.exception(f"Error with pixel. {e}")

    async def check_template(self, template, x, y, color):
        try:
            try:
                template_color = int(template.array[abs(x - template.sx), abs(y - template.sy)])
            except IndexError:
                log.debug(f"The index error in check_template, coords:{x}, {y} template:{template}")
                return
            # Pixel correct
            if color == template_color:
                # Is this pixel in the most recent alert?
                for p in template.pixels:
                    if p.x == x and p.y == y:
                        p.fixed = True
                        log.debug(f"Tracked pixel {p.x},{p.y} fixed to {Pixel.colors[color]}, was {p.log_color}. On {template}")
                        await self.send_embed(template)
            # Pixel incorrect
            elif color != template_color and template_color > -1:
                # If there is a current alert message, edit/add pixels to that
                if template.last_alert_message:
                    current_pixels = [p for p in template.pixels if p.alert_id == template.last_alert_message.id]

                    # If pixel is already being tracked, just update the data
                    for p in current_pixels:
                        if p.x == x and p.y == y:
                            p.damage_color = color
                            p.fixed = False
                            log.debug(f"Tracked pixel {p.x},{p.y} damaged to {p.log_color}. On {template}")
                            await self.send_embed(template)
                            return

                    # Pixel is not already tracked, add it to an old message
                    if len(current_pixels) < 10:
                        p = Pixel(color, x, y, template.last_alert_message.id, template.id)
                        template.pixels.append(p)
                        log.debug(f"Untracked pixel {p.x},{p.y} damaged to {p.log_color}. On {template}")
                        await self.send_embed(template)

                        # if adding our pixel increases it to 10, a new message needs to be created
                        if len(current_pixels) == 9:
                            log.debug(f"Clearing alert message for {template}")
                            template.last_alert_message = None
                        return

                # No current alert message, make a new one
                p = Pixel(color, x, y, "flag", template.id)
                template.pixels.append(p)
                log.debug(f"Untracked pixel {p.x},{p.y} damaged to {p.log_color}, new message created. On {template}")
                await self.send_embed(template)
        except Exception as e:
            log.exception(f"Error checking pixel. {e}")

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

            for p in template.pixels:
                if (template.last_alert_message and p.alert_id == template.last_alert_message.id) or \
                   (p.alert_id == "flag" and template.id == p.template_id):

                    damage_color = template.color(p.damage_color)
                    try:
                        template_color = template.color(
                            int(template.array[abs(p.x - template.sx), abs(p.y - template.sy)]))
                    except IndexError:
                        log.debug(f"The index error in send_embed, {p} {template}")
                        continue
                    text += template.s("alerts.alert_pixel").format(
                        p, damage_color, template_color, c="~~" if p.fixed else "")

            embed.add_field(name=template.s("alerts.recieved"), value=text, inline=False)
            embed.timestamp = datetime.datetime.now()

            try:
                if template.last_alert_message:
                    await template.last_alert_message.edit(embed=embed)
                else:
                    channel = self.bot.get_channel(template.alert_channel)
                    msg = await channel.send(embed=embed)
                    template.last_alert_message = msg
                    for p in template.pixels:
                        if p.alert_id == "flag":
                            p.alert_id = msg.id
            except discord.errors.HTTPException as e:
                log.debug(f"Exception sending message for {template}, {e}")

            # Release send lock
            template.sending = False
        except Exception as e:
            template.sending = False
            log.exception(f"Error sending/editing an embed. {e}")
