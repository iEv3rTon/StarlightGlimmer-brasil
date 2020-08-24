import asyncio
import io
import json
import logging
from time import time
from struct import unpack_from
import math
import uuid

import aiohttp
import requests
import websockets
from typing import Iterable
import socketio

from objects.chunks import BigChunk, ChunkPz, PxlsBoard
from objects.errors import HttpCanvasError, HttpGeneralError, NoJpegsError, NotPngError, TemplateHttpError
from utils.version import VERSION
from utils.canvases import url_templates


log = logging.getLogger(__name__)

useragent = {
    'User-Agent': 'StarlightGlimmer/{} (+https://github.com/BrickGrass/StarlightGlimmer)'.format(VERSION),
    'Cache-Control': 'no-cache'
}


# TODO: Cache chunks. I think that's actually what's getting me rate limited, requesting the
# exact same chunks in short succession.
class PixelZoneConnection:
    def __init__(self):
        self.sio = socketio.AsyncClient(binary=True, logger=log)
        self.ready = False

        self.chunk_lock = asyncio.Lock()
        self.wanted_chunks = []
        self.sent = []
        self.receieved_chunks = []

        self.player_count = None

        @self.sio.on("connect")
        async def on_connect():
            await self.sio.emit("hello")

        @self.sio.on("disconnect")
        async def on_disconnect():
            self.ready = False

        @self.sio.on("welcome")
        async def on_welcome():
            self.ready = True

        @self.sio.on("chunkBuffer")
        async def on_chunk(data):
            x = data.get("cx")
            y = data.get("cy")
            comp = data.get("comp")

            async with self.chunk_lock:
                chunk = next(c for c in self.wanted_chunks if c.x == x and c.y == y)
                if chunk:
                    chunk.load(comp)
                    self.wanted_chunks = [c for c in self.wanted_chunks if c.uuid != chunk.uuid]
                    self.receieved_chunks.append(chunk)
                    self.sent.remove(chunk.uuid)

        @self.sio.on("playerCounter")
        async def on_player(count):
            if isinstance(count, int):
                self.player_count = [time(), count]

    async def run(self):
        await self.sio.connect("https://pixelzone.io", headers=useragent)

        while True:
            if not self.ready:
                await asyncio.sleep(0.5)
            elif len(self.wanted_chunks) == 0:
                await asyncio.sleep(0.1)
            else:
                async with self.chunk_lock:
                    for chunk in self.wanted_chunks:
                        if not [uuid for uuid in self.sent if uuid == chunk.uuid]:
                            try:
                                await self.sio.emit("getChunk", {"x": chunk.x, "y": chunk.y})
                                self.sent.append(chunk.uuid)
                            except:
                                pass
                await asyncio.sleep(0.1)


async def fetch_chunks(bot, chunks: Iterable):
    """Calls the correct fetching function for all kinds of chunks"""
    c = next(iter(chunks))
    if type(c) is BigChunk:
        await _fetch_chunks_pixelcanvas(chunks)
    elif type(c) is ChunkPz:
        await _fetch_chunks_pixelzone(bot, chunks)
    elif type(c) is PxlsBoard:
        await _fetch_pxlsspace(chunks)


async def _fetch_chunks_pixelcanvas(bigchunks: Iterable[BigChunk]):
    """Fetches chunk data from pixelcanvas.io.

    Arguments:
    bigchunks - An iterable of a list of BigChunk objects."""
    async with aiohttp.ClientSession() as session:
        for bc in bigchunks:
            await asyncio.sleep(0)
            if not bc.is_in_bounds():
                continue
            data = None
            attempts = 0
            while attempts < 3:
                try:
                    async with session.get(bc.url, headers=useragent) as resp:
                        data = await resp.read()
                        if len(data) == 460800:
                            break
                except aiohttp.ClientPayloadError:
                    pass
                data = None
                attempts += 1
            if not data:
                raise HttpCanvasError('pixelcanvas')
            bc.load(data)


async def _fetch_chunks_pixelzone(bot, chunks: Iterable[ChunkPz]):
    """Fetches chunk data from pixelzone.io.

    Arguments:
    chunks - An iterable of ChunkPz objects."""
    chunks = [x for x in chunks if x.is_in_bounds()]
    if len(chunks) == 0:
        return
    uuids = [c.uuid for c in chunks]

    chunks_iter = iter(chunks)

    async def get_chunks():
        while True:
            async with bot.pz.chunk_lock:
                chunk = next(chunks_iter, None)
                if chunk is None:
                    break
                bot.pz.wanted_chunks.append(chunk)

            while True:
                async with bot.pz.chunk_lock:
                    if chunk.uuid in [c.uuid for c in bot.pz.receieved_chunks]:
                        break
                await asyncio.sleep(0.1)

    try:
        await asyncio.wait_for(get_chunks(), timeout=60)
    except asyncio.TimeoutError:
        async with bot.pz.chunk_lock:
            bot.pz.wanted_chunks = [c for c in bot.pz.wanted_chunks if c.uuid not in uuids]
        raise HttpCanvasError('pixelzone')
    finally:
        # Slightly mitigate the memory leak hell I have brought upon myself
        async with bot.pz.chunk_lock:
            bot.pz.receieved_chunks = [c for c in bot.pz.receieved_chunks if c.uuid not in uuids]


async def _fetch_pxlsspace(chunks: Iterable[PxlsBoard]):
    """Fetches chunk data from pxls.space.

    Arguments:
    chunks - An iterable of a list of PxlsBoard objects."""
    board = next(iter(chunks))
    async with aiohttp.ClientSession() as session:
        async with session.get("https://pxls.space/info", headers=useragent) as resp:
            info = json.loads(await resp.read())
        board.set_board_info(info)

        async with session.get("https://pxls.space/boarddata?={0:.0f}".format(time()), headers=useragent) as resp:
            board.load(await resp.read())


async def fetch_online_pixelcanvas():
    """Returns the number of users who are currently online pixelcanvas, integer."""
    async with aiohttp.ClientSession() as sess:
        async with sess.get("https://pixelcanvas.io/api/online", headers=useragent) as resp:
            if resp.status != 200:
                raise HttpGeneralError
            data = json.loads(await resp.read())
            return data['online']


async def fetch_online_pixelzone(bot):
    """Returns the number of users who are currently online pixelzone, integer."""
    if bot.pz.player_count:
        return bot.pz.player_count
    else:
        raise HttpCanvasError('pixelzone')


async def fetch_online_pxlsspace():
    """Returns the number of users who are currently online pxls.space, integer."""
    async with websockets.connect("wss://pxls.space/ws", extra_headers=useragent) as ws:
        async for msg in ws:
            d = json.loads(msg)
            if d['type'] == 'users':
                return d['count']


async def get_changelog(version):
    """Gets recent changelog data from my github page.

    Arguments:
    version - Version number, float.

    Returns:
    An iterable of all data from the changelog which matches the current version number.
    """
    url = "https://api.github.com/repos/DiamondIceNS/StarlightGlimmer/releases"
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise HttpGeneralError
            data = json.loads(await resp.read())
            return next((x for x in data if x['tag_name'] == "{}".format(VERSION)), None)


async def get_template(url, name):
    """Fetches and loads an image as a bytestream.

    Arguments:
    url - The url of the image, string.
    name - The name of the image, string.

    Returns:
    The image as a bytestream.
    """
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise TemplateHttpError(name)
            if resp.content_type == "image/jpg" or resp.content_type == "image/jpeg":
                raise NoJpegsError
            if resp.content_type != "image/png":
                raise NotPngError
            return io.BytesIO(await resp.read())


def get_template_blocking(url, name):
    resp = requests.get(url)
    if resp.status_code != 200:
        raise TemplateHttpError(name)
    if resp.headers["content-type"] == "image/jpg" or resp.headers["content-type"] == "image/jpeg":
        raise NoJpegsError
    if resp.headers["content-type"] != "image/png":
        raise NotPngError
    return io.BytesIO(resp.content)


class Tracker:
    def __init__(self, bot, ctx, canvas, pixels, embed, index):
        self.bot = bot
        self.ctx = ctx
        self.canvas = canvas
        self.pixels = pixels
        self.embed = embed
        self.index = index

        self.sending = False
        self.msg = None

    async def send_err_embed(self):
        if self.sending:
            return
        self.sending = True

        out = []
        for p in self.pixels:
            if p.current != p.target:
                current = self.ctx.s("color.{}.{}".format(self.canvas, p.current))
                target = self.ctx.s("color.{}.{}".format(self.canvas, p.target))
                url = url_templates[self.canvas].format(p.x, p.y)
                out.append(f"[({p.x},{p.y})]({url}) is {current}, should be {target}")
                if len(out) == 10:
                    out.append("...")
                    break
        if out == []:
            out = self.ctx.s("canvas.diff_fixed")
        else:
            out = "\n".join(out)

        try:
            current = self.embed.fields[self.index]
            self.embed.set_field_at(
                self.index,
                name=self.ctx.s("canvas.diff_error_title"),
                value=out,
                inline=False
            )
        except IndexError:
            self.embed.add_field(
                name=self.ctx.s("canvas.diff_error_title"),
                value=out,
                inline=False
            )

        await self.msg.edit(embed=self.embed)
        # Release send lock
        self.sending = False


class PixelCanvasTracker(Tracker):
    def __init__(self, *args):
        super().__init__(*args)
        self.fingerprint = uuid.uuid4().hex
        self._5_mins_time = time() + (60 * 5)

    async def connect_websocket(self, msg):
        self.msg = msg
        await self.send_err_embed()
        uri = f"wss://ws.pixelcanvas.io:8443/?fingerprint={self.fingerprint}"
        try:
            async with websockets.connect(uri, ssl=True) as ws:
                async for message in ws:
                    await self.on_message(message)
                    if time() > self._5_mins_time:
                        break
        except websockets.exceptions.ConnectionClosed:
            log.debug("Websocket disconnected in g!d -e")

        self.embed.set_footer(text=self.ctx.s("canvas.diff_timeout"))
        await self.msg.edit(embed=self.embed)

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


# TODO: Make this functional again by moving it's place event to the main connection.
# I could maybe use asyncio.Event or asyncio.Condition to listen to the main connection
# from here? I'll think on it a bit I guess. https://docs.python.org/3/library/asyncio-sync.html
class PixelZoneTracker(Tracker):
    def __init__(self, *args):
        super().__init__(*args)
        self.pz = PixelZoneConnection()

        loop = asyncio.get_event_loop()

        @self.pz.sio.on("place")
        async def on_message(pixels):
            for pixel in pixels:
                x = ((pixel >> 17) & 0b1111111111111) - 4096
                y = ((pixel >> 4) & 0b1111111111111) - 4096
                color = pixel & 0b1111

                for p in self.pixels:
                    if p.x == x and p.y == y:
                        p.current = color
                        loop.create_task(self.send_err_embed())

    async def connect_websocket(self, msg):
        self.msg = msg
        await self.send_err_embed()

        await self.pz.sio.connect("https://pixelzone.io")
        await asyncio.sleep(60 * 5)
        await self.pz.sio.disconnect()

        self.embed.set_footer(text=self.ctx.s("canvas.diff_timeout"))
        await self.msg.edit(embed=self.embed)


error_trackers = {
    "pixelcanvas": PixelCanvasTracker
}
