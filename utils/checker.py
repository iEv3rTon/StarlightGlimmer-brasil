import asyncio
import logging
import math
import time
import uuid
from io import BytesIO
import socket
from struct import unpack_from
import datetime

import aiohttp
import discord
from PIL import Image
import numpy as np
import websockets

from lang import en_US
from objects.bot_objects import GlimContext
from utils import converter, sqlite as sql

logger = logging.getLogger(__name__)


class Template:
    def __init__(self,
                 id: int, name: str, array: np.array, url: str, md5: str, sx: int, sy: int,
                 alert_channel: int, last_alert_message, sending: bool, pixels: list, gid):

        self.gid = gid
        self.id = id  # Unique identifier for each template, never changes, sourced from db
        self.name = name
        self.array = array  # Image array
        self.url = url  # Image url
        self.md5 = md5

        self.sx, self.sy = sx, sy  # sx = start x, ex = end x
        self.ex, self.ey = sx + array.shape[0], sy + array.shape[1]

        self.alert_channel = alert_channel  # Channel id
        self.last_alert_message = last_alert_message  # Message object
        self.sending = sending

        self.pixels = pixels  # list of pixel objects

    def s(self, string_id):
        return GlimContext.get_from_guild(self.gid, string_id)

    def color(self, index):
        return self.s(f"color.pixelcanvas.{index}")


class Pixel:
    def __init__(self, damage_color, x, y, alert_id, template_id):
        self.damage_color = damage_color  # int from 0-15, corresponds to an index in colors
        self.x = x
        self.y = y
        self.alert_id = alert_id  # Alert message this pixel is for
        self.template_id = template_id
        self.recieved = time.time()
        self.fixed = False

    def __repr__(self):
        return f"Pixel(color={self.damage_color}, x={self.x}, y={self.y}, aid={self.alert_id}, tid={self.template_id}, recieved={self.recieved}, fixed={self.fixed})"


class Checker:
    def __init__(self, bot):
        self.bot = bot
        self.templates = {}
        self.fingerprint = self.make_fingerprint()

        self.colors = []
        for x in range(16):
            self.colors.append(en_US.STRINGS.get(f"color.pixelcanvas.{x}", None))

    def make_fingerprint(self):
        return uuid.uuid4().hex

    def template_changed(self, t_db, t_list):
        return (t_db.x != t_list.sx) or \
               (t_db.y != t_list.sy) or \
               (t_db.md5 != t_list.md5) or \
               (t_db.name != t_list.name) or \
               (t_db.alert_id != t_list.alert_channel)

    async def generate_template(self, t, last_alert_message=None, pixels=[], sending=False):
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(t.url) as resp:
                    if resp.status == 200:
                        image = Image.open(BytesIO(await resp.read())).convert('RGBA')
                    else:
                        # Skip this template, it can get updated on the next round, no point retrying and delaying the others
                        logger.exception(f"File {t.name} could not be downloaded, status code: {resp.status}")
                        return None

            template = Template(t.id, t.name, converter.image_to_array(image), t.url, t.md5,
                                t.x, t.y, t.alert_id, last_alert_message, sending, pixels, t.gid)
            logger.debug("Generated template {0.name} from ({0.sx},{0.sy}) to ({0.ex},{0.ey}).".format(template))
            return template
        except Exception as e:
            logger.exception(f'Failed to generate template {t.name}. {e}')
            return None

    async def update(self):
        try:
            # Get the templates currently in the db
            templates = sql.template_get_all_alert()

            # Remove any templates from self.templates that are no longer in db
            db_t_ids = [t.id for t in templates]
            for id in self.templates.keys():
                if id not in db_t_ids:
                    t = self.templates.pop(id, None)
                    logger.debug(f"Template {t.name} no longer in the database, removed.")

            # Update any templates already in self.templates that have changed
            for id in self.templates.keys():
                for t in templates:
                    if id != t.id:
                        continue
                    old_t = self.templates.get(id)
                    if not self.template_changed(t, old_t):
                        continue

                    # Pass in the old state to preserve it
                    tp = await self.generate_template(
                        t, old_t.last_alert_message, old_t.pixels, old_t.sending)
                    self.templates[id] = tp if tp is not None else old_t

            # Add any new templates from db to self.templates
            for t in templates:
                if t.id in self.templates.keys():
                    continue
                tp = await self.generate_template(t)
                if tp is None:
                    continue

                self.templates[tp.id] = tp

            # Do cleanup on data
            _5_mins = 60 * 30
            now = time.time()
            channel_messages = {}
            for id in self.templates.keys():
                t = self.templates.get(id)
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
                    t.last_alert_message = None
                    logger.debug(f"Alert message for {t.name} is more than 5 messages ago, cleared.")

                # Clean up old pixel data
                for p in t.pixels:
                    # Pixels recieved more than 5 mins ago that are not attached to the current alert msg will be cleared
                    if (now - p.recieved) > _5_mins and p.alert_id != t.last_alert_message.id:
                        t.pixels.remove(p)
                        logger.debug(f"Pixel {p.x},{p.y} colour:{self.colors[p.damage_color]} template:{t.name} cleared.")

        except Exception as e:
            logger.exception(f'Failed to update. {e}')

    async def run_websocket(self):
        while True:
            logger.debug("Connecting to websocket...")
            try:
                url = f"wss://ws.pixelcanvas.io:8443/?fingerprint={self.fingerprint}"
                async with websockets.connect(url) as ws:
                    async for message in ws:
                        await self.on_message(message)
            except websockets.exceptions.ConnectionClosed:
                logger.debug("Websocket disconnected.")
            except socket.gaierror:
                logger.debug("Temporary failure in name resolution.")
            except Exception as e:
                logger.exception(f"Error launching! {e}")

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

                # logger.debug("Pixel placed, ({0},{1}) colour:{2}".format(x, y, self.colors[color]))

                for id in self.templates.keys():
                    template = self.templates.get(id)
                    await self.check_template(template, x, y, color)
        except Exception as e:
            logger.exception(f"Error with pixel. {e}")

    async def check_template(self, template, x, y, color):
        try:
            # Is pixel in the start to end range of this template
            if template.sx <= x < template.ex and template.sy <= y < template.ey:
                try:
                    template_color = int(template.array[abs(x - template.sx), abs(y - template.sy)])
                except IndexError:
                    logger.debug(f"The index error in check_template, pixel coords:{x},{y} colour:{color} template:{template.name} template start:{template.sx},{template.sy} template end:{template.ex},{template.ey}")
                    return
                # Pixel correct
                if color == template_color:
                    # Is this pixel in the most recent alert?
                    for p in template.pixels:
                        if p.x == x and p.y == y:
                            p.fixed = True
                            logger.debug(f"Tracked pixel {x},{y} on {template.name} fixed to {self.colors[color]}.")
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
                                logger.debug(f"Tracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}.")
                                await self.send_embed(template)
                                return

                        # Pixel is not already tracked, add it to an old message
                        if len(current_pixels) < 10:
                            template.pixels.append(Pixel(color, x, y, template.last_alert_message.id, template.id))
                            logger.debug(f"Untracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}.")
                            await self.send_embed(template)

                            # if adding our pixel increases it to 10, a new message needs to be created
                            if len(current_pixels) == 9:
                                template.last_alert_message = None
                                logger.debug(f"{template.name}'s alert message was cleared.")
                            return

                    # No current alert message, make a new one
                    template.pixels.append(Pixel(color, x, y, "flag", template.id))
                    logger.debug(f"Untracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}, new message created.")
                    await self.send_embed(template)
        except Exception as e:
            logger.exception(f"Error checking pixel. {e}")

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
                        logger.debug(f"The index error in send_embed, pixel coords:{p.x},{p.y} colour:{p.damage_color} template:{template.name} template start:{template.sx},{template.sy} template end:{template.ex},{template.ey}")
                        continue
                    text += template.s("alerts.alert_pixel").format(
                        p, damage_color, template_color, c="~~" if p.fixed else "")

            embed.add_field(name=template.s("alerts.recieved"), value=text, inline=False)
            embed.timestamp = datetime.datetime.now()

            try:
                if template.last_alert_message:
                    await template.last_alert_message.edit(embed=embed)
                    logger.debug(f"Alert message for {template.name} edited.")
                else:
                    channel = self.bot.get_channel(template.alert_channel)
                    msg = await channel.send(embed=embed)
                    template.last_alert_message = msg
                    for p in template.pixels:
                        if p.alert_id == "flag":
                            p.alert_id = msg.id
                    logger.debug(f"New alert message for {template.name} created.")
            except discord.errors.HTTPException as e:
                logger.debug(f"Exception sending message for {template.name}, {e}")

            # Release send lock
            template.sending = False
        except Exception as e:
            template.sending = False
            logger.exception(f"Error sending/editing an embed. {e}")
