import asyncio
import logging
import math
import threading
import time
import uuid
from io import BytesIO
from struct import unpack_from
import datetime

import discord
from PIL import Image
import numpy as np
import requests
import websocket

from lang import en_US
from utils import converter, sqlite as sql

logger = logging.getLogger(__name__)


# Shoving this into a coroutine to get past it not wanting to use a db connection from a separate thread lol
async def template_get_all_alert():
    return sql.template_get_all_alert()


class Template:
    def __init__(self,
                 id: int, name: str, array: np.array, url: str, md5: str, sx: int, sy: int,
                 alert_channel: int, last_alert_message, sending: bool, pixels: list):

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


class Pixel:
    def __init__(self, damage_color, x, y, alert_id, template_id):
        self.damage_color = damage_color  # int from 0-15, corresponds to an index in colors
        self.x = x
        self.y = y
        self.alert_id = alert_id  # Alert message this pixel is for
        self.template_id = template_id
        self.recieved = time.time()
        self.fixed = False


class Checker:
    URL = 'https://pixelcanvas.io/'
    TEMPLATE_PATH = ''
    HEADER_USER_AGENT = {
        'User-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.86 Safari/537.36',
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3'
    }
    HEADERS = {
        'User-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_3) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/73.0.3683.86 Safari/537.36',
        'accept': 'application/json',
        'content-type': 'application/json',
        'Host': 'pixelcanvas.io',
        'Origin': URL,
        'Referer': URL
    }

    def __init__(self, bot):
        self.bot = bot
        self.templates = []
        self.fingerprint = self.make_fingerprint()

        self.data_update_thread = threading.Thread(target=self.update)
        self.data_update_thread.setDaemon(True)
        self.data_update_thread.start()
        self.working = False

        self.colors = []
        for x in range(16):
            self.colors.append(en_US.STRINGS.get(f"color.pixelcanvas.{x}", None))

    def make_fingerprint(self):
        return uuid.uuid4().hex

    def template_changed(self, t_db, t_list):
        return (t_db.x != t_list.sx) or (t_db.y != t_list.sy) or (t_db.md5 != t_list.md5) or (t_db.name != t_list.name)

    def generate_template(self, t, last_alert_message=None, pixels=[], sending=False):
        try:
            response = requests.get(t.url)
            if response.status_code == 200:
                image = Image.open(BytesIO(response.content)).convert('RGBA')
            else:
                # Skip this template, it can get updated on the next round, no point retrying and delaying the others
                logger.exception(f"File {t.name} could not be downloaded, status code: {response.status_code}")
                return None

            template = Template(t.id, t.name, converter.image_to_array(image), t.url, t.md5,
                                t.x, t.y, t.alert_id, last_alert_message, sending, pixels)
            logger.debug("Generated template {0.name} from ({0.sx},{0.sy}) to ({0.ex},{0.ey}).".format(template))
            return template
        except:
            logger.exception('Failed to generate template {}.'.format(t.name))
            return None

    def update(self):
        asyncio.set_event_loop(self.bot.loop)
        while True:
            try:
                # Get the templates currently in the db
                task = asyncio.ensure_future(template_get_all_alert())
                while not task.done():
                    time.sleep(0.1)
                templates = task.result()

                # Remove any templates from self.templates that are no longer in db
                db_t_ids = [t.id for t in templates]
                for t in self.templates:
                    if t.id not in db_t_ids:
                        self.templates.remove(t)

                # Update any templates already in self.templates that have changed
                for i, t in enumerate(self.templates):
                    for t_ in templates:
                        if t.id == t_.id:
                            if self.template_changed(t_, t):
                                # Pass in the old state to preserve it
                                tp = self.generate_template(t_, t.last_alert_message, t.pixels, t.sending)
                                self.templates[i] = tp if tp is not None else t

                # Add any new templates from db to self.templates
                list_t_ids = [t.id for t in self.templates]
                for t in templates:
                    if t.id not in list_t_ids:
                        tp = self.generate_template(t)
                        if tp is not None:
                            self.templates.append(tp)

                # Do cleanup on data
                for t in self.templates:
                    if t.last_alert_message:
                        # Is the most recent alert within the last 5 messages in it's alert channel?
                        alert_channel = self.bot.get_channel(t.alert_channel)
                        task = asyncio.ensure_future(alert_channel.history(limit=5).flatten())
                        while task.done() is not True:
                            pass
                        messages = task.result()
                        # The channel could have been cleared during the time we spend collecting the history
                        if t.last_alert_message:
                            msg_found = False
                            for msg in messages:
                                if msg.id == t.last_alert_message.id:
                                    msg_found = True
                            # Clear the alert message if it isn't recent anymore so new alerts will be at the bottom of the channel
                            if not msg_found:
                                t.last_alert_message = None
                                logger.debug(f"Alert message for {t.name} more than 5 messages ago, cleared.")

                    # Clean up old pixel data
                    for p in t.pixels:
                        delta = time.time() - p.recieved
                        _5_mins = 60 * 30
                        # Pixels recieved more than 5 mins ago that are not attached to the current alert msg will be cleared
                        if t.last_alert_message:
                            if delta > _5_mins and p.alert_id != t.last_alert_message.id:
                                t.pixels.remove(p)
                                logger.debug(f"Pixel {p.x},{p.y} colour:{p.damage_color} template:{t.name} received more than 5 mins ago and not attached to current message, cleared.")

                # Sleep for 5m
                time.sleep(300)
            except:
                logger.exception('Failed to update.')

    def get(self, route: str, stream: bool = False):
        return requests.get(Checker.URL + route, stream=stream, headers=Checker.HEADER_USER_AGENT)

    def fetch_websocket(self):
        return 'wss://ws.pixelcanvas.io:8443'

    def connect_websocket(self):
        def on_message(ws, message):
            asyncio.set_event_loop(self.bot.loop)
            try:
                if unpack_from('B', message, 0)[0] == 193:
                    x = unpack_from('!h', message, 1)[0]
                    y = unpack_from('!h', message, 3)[0]
                    a = unpack_from('!H', message, 5)[0]
                    number = (65520 & a) >> 4
                    x = int(x * 64 + ((number % 64 + 64) % 64))
                    y = int(y * 64 + math.floor(number / 64))
                    color = 15 & a

                    # Logs *all* incoming pixels from websocket, might be a bit too spammy, enable if really needed
                    # logger.debug('Pixel placed, coords:({0},{1}) colour:{2}'.format(x,y,self.colors[color]))
                    for template in self.templates:
                        check_template(self, template, x, y, color)
            except:
                logger.exception("Error with pixel.")

        def on_error(ws, exception):
            if exception == websocket._exceptions.WebsocketClosedConnectionException:
                logger.debug("Pixelcanvas closed the connection.")
            else:
                logger.exception('Websocket error: {}'.format(exception))
            ws.close()

        def on_close(ws):
            logger.debug('Websocket closed.')
            self.working = False
            open_connection()

        def on_open(ws):
            logger.debug('Websocket opened.')
            self.working = True
            self.fingerprint = self.make_fingerprint()

        def open_connection():
            if not self.working:
                logger.debug("Bot loading websocket...")
                url = self.fetch_websocket()
                ws = websocket.WebSocketApp(url + '/?fingerprint=' + self.fingerprint, on_message=on_message,
                                            on_open=on_open, on_close=on_close, on_error=on_error)

                def worker(ws):
                    asyncio.set_event_loop(self.bot.loop)
                    ws.run_forever()

                thread = threading.Thread(target=worker, args=(ws,))
                thread.setDaemon(True)
                thread.start()
            else:
                logger.error('Bot attempted to open socket when already open.')

        def check_template(self, template, x, y, color):
            try:
                # Is pixel in the start to end range of this template
                if template.sx <= x < template.ex and template.sy <= y < template.ey:
                    try:
                        template_color = int(template.array[abs(x-template.sx), abs(y-template.sy)])
                    except IndexError:
                        logger.debug(f"The index error in check_template, pixel coords:{x},{y} colour:{color} template:{template.name} template start:{template.sx},{template.sy} template end:{template.ex},{template.ey}")
                        return
                    # Pixel correct
                    if color == template_color:
                        # Is this pixel in the most recent alert?
                        for p in template.pixels:
                            if p.x == x and p.y == y:
                                p.fixed = True
                                asyncio.ensure_future(send_embed(self, template))
                                logger.debug(f"Tracked pixel {x},{y} on {template.name} fixed to {self.colors[color]}.")
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
                                    asyncio.ensure_future(send_embed(self, template))
                                    logger.debug(f"Tracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}. Pixel thinks it is for template_id {p.template_id}")
                                    return

                            # Pixel is not already tracked, add it to an old message
                            if len(current_pixels) < 10:
                                template.pixels.append(Pixel(color, x, y, template.last_alert_message.id, template.id))
                                asyncio.ensure_future(send_embed(self, template))
                                logger.debug(f"Untracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}, added to previous message.")

                                # if adding our pixel increases it to 10, a new message needs to be created
                                if len(current_pixels) == 9:
                                    template.last_alert_message = None
                                    logger.debug(f"{template.name}'s alert message was cleared.")
                                return

                        # No current alert message, make a new one
                        template.pixels.append(Pixel(color, x, y, "flag", template.id))
                        asyncio.ensure_future(send_embed(self, template))
                        logger.debug(f"Untracked pixel {x},{y} on {template.name} damaged to {self.colors[color]}, new message created.")
            except:
                logger.exception("Error checking pixel.")

        async def send_embed(self, template):
            try:
                if template.sending:
                    logger.debug(f"{template.name} is already sending, skipped. sending:{template.sending}")
                    return
                template.sending = True
                logger.debug(f"Begun sending embed for {template.name} sending:{template.sending}")

                embed = discord.Embed(title="{} took damage!".format(template.name), description="Messages that are crossed out have been fixed.")
                embed.set_thumbnail(url=template.url)
                text = ""

                for p in template.pixels:
                    if (template.last_alert_message and p.alert_id == template.last_alert_message.id) or (p.alert_id == "flag" and template.id == p.template_id):
                        damage_color = self.colors[p.damage_color]
                        try:
                            template_color = self.colors[int(template.array[abs(p.x-template.sx), abs(p.y-template.sy)])]
                        except IndexError:
                            logger.debug(f"The index error in send_embed, pixel coords:{p.x},{p.y} colour:{p.damage_color} template:{template.name} template start:{template.sx},{template.sy} template end:{template.ex},{template.ey}")
                            continue
                        crossed_out = "~~" if p.fixed else ""
                        text += f"{crossed_out}[@{p.x},{p.y}](https://pixelcanvas.io/@{p.x},{p.y}) painted **{damage_color}**, should be **{template_color}**.{crossed_out}\n"

                embed.add_field(name="Received:", value=text, inline=False)
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
                logger.debug(f"Finished sending embed for {template.name} sending:{template.sending}")
            except:
                template.sending = False
                logger.exception("Error sending/editing an embed.")

        try:
            logger.debug("Opener launching...")
            open_connection()
        except:
            logger.exception("Error launching!")
