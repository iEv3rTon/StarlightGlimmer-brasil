import asyncio
import math
import threading
import time
import logging
from struct import unpack_from
import uuid

import discord
import websocket

log = logging.getLogger(__name__)

class Pixel:
    def __init__(self, current, target, x, y):
        self.current = current
        self.target = target
        self.x = x
        self.y = y

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

    def __init__(self, bot, ctx, canvas, pixels):
        self.bot = bot
        self.ctx = ctx
        self.fingerprint = uuid.uuid4().hex
        self._5_mins_time = time.time() + 60*5
        self.canvas = canvas
        self.pixels = pixels
        self.sending = False
        self.msg = None
        self.content = ""
        self.timeout_string = self.ctx.s("canvas.diff_timeout")  # Was failing weirdly when called outside of init

        asyncio.ensure_future(self.send_err_embed())

    def connect_websocket(self):
        def on_message(ws, message):
            asyncio.set_event_loop(self.bot.loop)
            if self._5_mins_time < time.time():
                self.content = self.timeout_string
                asyncio.ensure_future(self.send_err_embed())
                ws.close()
            if unpack_from('B', message, 0)[0] == 193:
                x = unpack_from('!h', message, 1)[0]
                y = unpack_from('!h', message, 3)[0]
                a = unpack_from('!H', message, 5)[0]
                number = (65520 & a) >> 4
                x = int(x * 64 + ((number % 64 + 64) % 64))
                y = int(y * 64 + math.floor(number / 64))
                color = 15 & a

                asyncio.ensure_future(self.check_pixels(x, y, color, ws))

        def on_error(ws, exception):
            log.exception(exception)
            self.content = self.timeout_string
            asyncio.ensure_future(self.send_err_embed())

        def on_close(ws):
            pass

        def on_open(ws):
            pass

        url = "wss://ws.pixelcanvas.io:8443"
        ws = websocket.WebSocketApp(
            url + '/?fingerprint=' + self.fingerprint, on_message=on_message,
            on_open=on_open, on_close=on_close, on_error=on_error)

        def worker(ws):
            asyncio.set_event_loop(self.bot.loop)
            ws.run_forever()

        thread = threading.Thread(target=worker, args=(ws,))
        thread.setDaemon(True)
        thread.start()

    async def check_pixels(self, x, y, color, ws):
        for p in self.pixels:
            if p.x == x and p.y == y:
                p.current = color
                check = await self.send_err_embed()
                if check == True:
                    ws.close()

    async def send_err_embed(self):
        if self.sending:
            return
        self.sending = True

        embed = discord.Embed()
        out = []
        for p in self.pixels:
            if p.current != p.target:
                current = self.ctx.s("color.{}.{}".format(self.canvas, p.current))
                target = self.ctx.s("color.{}.{}".format(self.canvas, p.target))
                out.append(f"[({p.x},{p.y})](https://pixelcanvas.io/@{p.x},{p.y}) is {current}, should be {target}")
                if len(out) == 10:
                    out.append("...")
                    break
        if out == []:
            out = self.ctx.s("canvas.diff_fixed")
        else:
            out = "\n".join(out)
        embed.add_field(name=self.ctx.s("canvas.diff_error_title"), value=out)

        if self.msg:
            await self.msg.edit(embed=embed, content=self.content)
        else:
            self.msg = await self.ctx.send(embed=embed, content=self.content)
        # Release send lock
        self.sending = False
