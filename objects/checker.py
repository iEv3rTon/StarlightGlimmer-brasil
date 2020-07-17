import asyncio
import logging
import math
from struct import unpack_from
import time
import uuid

import discord
import websockets

log = logging.getLogger(__name__)


class Pixel:
    def __init__(self, current, target, x, y):
        self.current = current
        self.target = target
        self.x = x
        self.y = y


class Checker:
    def __init__(self, bot, ctx, canvas, pixels):
        self.bot = bot
        self.ctx = ctx
        self.fingerprint = uuid.uuid4().hex
        self._5_mins_time = time.time() + (60 * 5)
        self.canvas = canvas
        self.pixels = pixels
        self.sending = False
        self.msg = None
        self.embed = None

    async def connect_websocket(self):
        await self.send_err_embed()

        uri = f"wss://ws.pixelcanvas.io:8443/?fingerprint={self.fingerprint}"
        async with websockets.connect(uri, ssl=True) as ws:
            async for message in ws:
                await self.on_message(message)
                if time.time() > self._5_mins_time:
                    break

        self.embed.set_footer(text=self.ctx.s("canvas.diff_timeout"))
        await self.msg.edit(embed=self.embed)

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
        self.embed = embed

        if self.msg:
            await self.msg.edit(embed=self.embed)
        else:
            self.msg = await self.ctx.send(embed=self.embed)
        # Release send lock
        self.sending = False

    async def on_message(self, message):
        if unpack_from('B', message, 0)[0] == 193:
            x = unpack_from('!h', message, 1)[0]
            y = unpack_from('!h', message, 3)[0]
            a = unpack_from('!H', message, 5)[0]
            number = (65520 & a) >> 4
            x = int(x * 64 + ((number % 64 + 64) % 64))
            y = int(y * 64 + math.floor(number / 64))
            color = 15 & a

            for p in self.pixels:
                if p.x == x and p.y == y:
                    p.current = color
                    await self.send_err_embed()
