import asyncio
from time import time
import logging
import uuid
import socket
from struct import unpack_from
import math
import json

import numpy as np
import socketio
from PIL import Image
import websockets

from objects.chunks import ChunkPz
from utils import converter, canvases, colors, http


log = logging.getLogger(__name__)


class LongrunningWSConnection:
    def __init__(self, bot):
        self.bot = bot

        self.listener_lock = asyncio.Lock()
        self.listeners = {}

    async def add_listener(self, listener):
        l_uuid = uuid.uuid4()
        async with self.listener_lock:
            self.listeners[l_uuid] = listener
            log.debug(f"Listener {listener} added with uuid {l_uuid}")
        return l_uuid

    async def remove_listener(self, l_uuid):
        async with self.listener_lock:
            del self.listeners[l_uuid]
            log.debug(f"Listener with uuid {l_uuid} removed")


class PixelZoneConnection(LongrunningWSConnection):
    def __init__(self, *args):
        super().__init__(*args)

        self.sio = socketio.AsyncClient(binary=True, logger=log)
        self.ready = False

        self.chunk_lock = asyncio.Lock()
        self.chunks = {}
        self.chunk_queue = asyncio.Queue()

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
                image = ChunkPz.load(comp)
                self.chunks[f"{x}_{y}"] = CachedPZChunk(x, y, image)

        @self.sio.on("playerCounter")
        async def on_player(count):
            if isinstance(count, int):
                self.player_count = [time(), count]

        @self.sio.on("place")
        async def on_message(pixels):
            pixels = pixels.replace(" ", "").replace("[", "").replace("]", "")
            pixels = [int(pixel) for pixel in pixels.split(",")]

            for pixel in pixels:
                x = (pixel >> 17) - canvases.PZ_MAP_LENGTH_HALVED
                y = ((pixel >> 4) & 0b1111111111111) - canvases.PZ_MAP_LENGTH_HALVED
                color = pixel & 0b1111

                cx, cy = ChunkPz.chunk_from_coords(x, y)

                async with self.chunk_lock:
                    cached = self.chunks.get(f"{cx}_{cy}", None)
                    if cached:
                        cached.add_pixel(x, y, color)

                async with self.listener_lock:
                    for _, listener in self.listeners.items():
                        self.bot.loop.create_task(listener(x, y, color, "pixelzone"))

    async def requester(self):
        while True:
            x, y = await self.chunk_queue.get()

            while not self.ready:
                asyncio.sleep(0.5)

            try:
                await self.sio.emit("getChunk", {"x": x, "y": y})
                self.chunk_queue.task_done()
            except Exception:
                log.exception("Error sending chunk request to pixelzone.io")

    async def expirer(self):
        _1_hour = 60 * 60

        while True:
            async with self.chunk_lock:
                to_remove = []
                for key, cached in self.chunks.items():
                    if cached.age > _1_hour:
                        to_remove.append(key)

                for key in to_remove:
                    del self.chunks[key]

            await asyncio.sleep(60)

    async def run(self):
        try:
            # Once the connection initially succeeds, sio handles all reconnects
            log.debug("Connecting to pixelzone.io websocket...")
            await self.sio.connect("https://pixelzone.io", headers=http.useragent)
        except Exception:
            log.exception("Pixelzone connection failed to open")
        self.bot.loop.create_task(self.requester())
        self.bot.loop.create_task(self.expirer())


class CachedPZChunk:
    def __init__(self, cx, cy, image):
        self.cx = cx
        self.cy = cy

        image = image.convert("RGBA")
        self.array = converter.image_to_array(image, "pixelzone")

        self.created = time()

    @property
    def age(self):
        now = time()
        return now - self.created

    def add_pixel(self, px, py, color):
        rel_x = px % canvases.PZ_CHUNK_LENGTH
        rel_y = py % canvases.PZ_CHUNK_LENGTH
        self.array[rel_x, rel_y] = color

    def to_image(self):
        width, height = self.array.shape
        pil_array = np.zeros((height, width, 3), dtype=np.int8)

        for x in range(width):
            for y in range(height):
                r, g, b = colors.pixelzone[self.array[x, y]]
                pil_array[y, x, 0] = r
                pil_array[y, x, 1] = g
                pil_array[y, x, 2] = b

        image = Image.fromarray(pil_array, "RGB")
        return image


class PixelCanvasConnection(LongrunningWSConnection):
    def __init__(self, *args):
        super().__init__(*args)
        self.fingerprint = uuid.uuid4().hex

        self.last_failure = None
        self.failures = 0

    async def on_message(self, message):
        if unpack_from("B", message, 0)[0] == 193:
            x = unpack_from('!h', message, 1)[0]
            y = unpack_from('!h', message, 3)[0]
            a = unpack_from('!H', message, 5)[0]
            number = (65520 & a) >> 4
            x = int(x * 64 + ((number % 64 + 64) % 64))
            y = int(y * 64 + math.floor(number / 64))
            color = 15 & a

            async with self.listener_lock:
                for _, listener in self.listeners.items():
                    self.bot.loop.create_task(listener(x, y, color, "pixelcanvas"))

    async def run(self):
        while True:
            if self.last_failure:
                failure_delta = time() - self.last_failure
                if failure_delta > 60 * 5:
                    self.failures += 1
                    await asyncio.sleep(2 ** self.failures)
                else:
                    self.failures = 0

            log.debug("Connecting to pixelcanvas.io websocket...")
            try:
                url = f"wss://ws.pixelcanvas.io:8443/?fingerprint={self.fingerprint}"
                async with websockets.connect(url, extra_headers=http.useragent) as ws:
                    async for message in ws:
                        self.bot.loop.create_task(self.on_message(message))
            except websockets.exceptions.ConnectionClosed:
                log.debug("Pixelcanvas.io websocket disconnected.")
            except socket.gaierror:
                log.debug("Temporary failure in name resolution for pixelcanvas.io.")
            except Exception:
                log.exception("Error with pixelcanvas websocket!")

            self.last_failure = time()


class PxlsSpaceConnection(LongrunningWSConnection):
    def __init__(self, *args):
        super().__init__(*args)
        self.player_count = None

        self.last_failure = None
        self.failures = 0

    async def on_message(self, data):
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            log.debug("Error decoding pxls.space websocket message.")
            return

        if message["type"] == "pixel":
            for pixel in message["pixels"]:
                x = int(pixel["x"])
                y = int(pixel["y"])
                color = int(pixel["color"])

                async with self.listener_lock:
                    for _, listener in self.listeners.items():
                        self.bot.loop.create_task(listener(x, y, color, "pxlsspace"))

        elif message["type"] == "users":
            self.player_count = [time(), int(message["count"])]

    async def run(self):
        while True:
            if self.last_failure:
                failure_delta = time() - self.last_failure
                if failure_delta > 60 * 5:
                    self.failures += 1
                    await asyncio.sleep(2 ** self.failures)
                else:
                    self.failures = 0

            log.debug("Connecting to pxls.space websocket...")
            try:
                async with websockets.connect("wss://pxls.space/ws", extra_headers=http.useragent) as ws:
                    async for message in ws:
                        self.bot.loop.create_task(self.on_message(message))
            except websockets.exceptions.ConnectionClosed:
                log.debug("Pxls.space websocket disconnected.")
            except socket.gaierror:
                log.debug("Temporary failure in name resolution for Pxls.space.")
            except Exception:
                log.exception("Error with Pxls.space websocket!")

            self.last_failure = time()
