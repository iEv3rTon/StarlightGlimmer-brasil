import asyncio
from time import time
import logging
import uuid
import socket
from struct import unpack_from
import math
import json
import datetime

import numpy as np
import socketio
from PIL import Image
import websockets
from aiohttp_sse_client import client as sse_client

from objects.chunks import ChunkPz
from objects.database_models import session_scope, Pixel, Canvas, Online
from utils import converter, canvases, colors, http, channel_log


log = logging.getLogger(__name__)
SEVEN_DAYS = 7 * 24 * 60 * 60


class LongrunningWSConnection:
    canvas = None

    def __init__(self, bot):
        self.bot = bot

        self.alive = None

        self.listener_lock = asyncio.Lock()
        self.listeners = {}

    async def add_listener(self, listener):
        l_uuid = uuid.uuid4()
        async with self.listener_lock:
            self.listeners[l_uuid] = listener
            log.debug(f"Listener {listener} for canvas {self.canvas} added with uuid {l_uuid}")
        return l_uuid

    async def remove_listener(self, l_uuid):
        async with self.listener_lock:
            del self.listeners[l_uuid]
            log.debug(f"Listener for canvas {self.canvas} with uuid {l_uuid} removed")

    def update_online(self, time, count):
        with session_scope() as session:
            canvas_query = session.query(Canvas).filter_by(nick=self.canvas)
            canvas = canvas_query.first()
            if not canvas:
                log.exception(f"No row found for canvas {self.canvas}, online stats were not updated.")
                return

            session.add(Online(
                time=datetime.datetime.fromtimestamp(time, tz=datetime.timezone.utc),
                count=count,
                canvas=canvas))

    @staticmethod
    async def update_placements(x, y, color, canvas_name):
        with session_scope() as session:
            canvas = session.query(Canvas).filter_by(nick=canvas_name).first()
            if not canvas:
                log.exception(f"No row found for canvas {canvas_name}, placement stats were not updated.")
                return

            session.add(Pixel(x=x, y=y, color=color, canvas=canvas))


class PixelZoneConnection(LongrunningWSConnection):
    def __init__(self, *args):
        super().__init__(*args)
        self.canvas = "pixelzone"

        self.sio = socketio.AsyncClient(logger=log)
        self.ready = False

        self.retry = True
        self.last_failure = None
        self.failures = 0

        self.chunk_lock = asyncio.Lock()
        self.chunks = {}
        self.chunk_queue = asyncio.Queue()
        self.requested_queue = asyncio.Queue()

        self.player_count = None

        @self.sio.on("connect")
        async def on_connect():
            self.alive = time()
            await self.sio.emit("hello")

        @self.sio.on("disconnect")
        async def on_disconnect():
            self.ready = False

        @self.sio.on("welcome")
        async def on_welcome():
            self.alive = time()
            self.ready = True

        @self.sio.on("chunkBuffer")
        async def on_chunk(data):
            self.alive = time()
            x = data.get("cx")
            y = data.get("cy")
            comp = data.get("comp")

            async with self.chunk_lock:
                image = ChunkPz.load(comp)
                self.chunks[f"{x}_{y}"] = CachedPZChunk(x, y, image)

        @self.sio.on("playerCounter")
        async def on_player(count):
            now = time()

            self.alive = now

            count = int(count)
            self.player_count = [now, count]

            self.update_online(now, count)

        @self.sio.on("place")
        async def on_message(pixels):
            self.alive = time()
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
                await asyncio.sleep(0.5)

            try:
                await self.sio.emit("getChunk", {"x": x, "y": y})
                self.chunk_queue.task_done()
                self.bot.loop.create_task(self.followup(x, y))
            except Exception:
                log.exception("Error sending chunk request to pixelzone.io")

    async def followup(self, x, y):
        await asyncio.sleep(0.5)
        await self.requested_queue.put([x, y])

    async def nanny(self):
        while True:
            x, y = await self.requested_queue.get()

            chunk_key = f"{x}_{y}"

            async with self.chunk_lock:
                if chunk_key not in list(self.chunks.keys()):
                    await self.chunk_queue.put([x, y])

            self.requested_queue.task_done()

    async def expirer(self):
        _5_hours = 60 * 60 * 5

        while True:
            async with self.chunk_lock:
                to_remove = []
                for key, cached in self.chunks.items():
                    if cached.age > _5_hours:
                        to_remove.append(key)

                for key in to_remove:
                    del self.chunks[key]

            await asyncio.sleep(60)

    async def run(self):
        await self.add_listener(self.update_placements)

        self.bot.loop.create_task(self.requester())
        self.bot.loop.create_task(self.expirer())
        self.bot.loop.create_task(self.nanny())

        while True:
            await self.connect()

            while True:
                try:
                    await self.sio.wait()
                except ConnectionResetError:
                    log.warning("Pixelzone.io connection reset by host.")
                except Exception:
                    log.exception("Error running pixelzone.io websocket.")

                # We disconnected, invalidate all chunks
                async with self.chunk_lock:
                    self.chunks = {}

                # Give the built-in reconnect time to work
                await asyncio.sleep(60 * 5)

                # S.io reconnect didn't work, disconnect and try to start from scratch
                if not self.sio.connected and not self.ready:
                    await self.sio.disconnect()
                    break

    async def connect(self):
        failure_count = 0

        # Retry until initial connection succeeds.
        while True:
            if failure_count:
                sleep_time = min(60 * 5, 2 ** failure_count)
                self.bot.loop.create_task(channel_log(self.bot, f"Failure {failure_count} to connect to pixelzone.io, waiting {sleep_time} seconds before attempting reconnection..."))
                await asyncio.sleep(sleep_time)

            try:
                log.debug("Connecting to pixelzone.io websocket...")
                self.bot.loop.create_task(channel_log(self.bot, "Connecting to pixelzone.io websocket..."))
                await self.sio.connect("https://pixelzone.io", headers=http.useragent)
            except socketio.exceptions.ConnectionError:
                log.exception("Pixelzone connection refused.")
            except Exception:
                log.exception("Other error during initial pixelzone connection.")
            else:
                break

            failure_count += 1


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
        self.canvas = "pixelcanvas"

        self.fingerprint = uuid.uuid4().hex

        self.last_failure = None
        self.failures = 0

    async def on_message(self, message):
        self.alive = time()

        try:
            data = json.loads(message.data)
            x = data["x"]
            y = data["y"]
            color = data["color"]
        except Exception:
            log.exception("Error decoding json message from pixelcanvas event source.")

        async with self.listener_lock:
            for _, listener in self.listeners.items():
                self.bot.loop.create_task(listener(x, y, color, "pixelcanvas"))

    async def run(self):
        await self.add_listener(self.update_placements)

        while True:
            if self.last_failure:
                failure_delta = time() - self.last_failure
                if failure_delta > 60 * 5:
                    self.failures += 1
                    await asyncio.sleep(2 ** self.failures)
                else:
                    self.failures = 0

            log.debug("Connecting to pixelcanvas.io event source...")

            url = f"https://pixelcanvas.io/events?fingerprint={self.fingerprint}"
            try:
                async with sse_client.EventSource(url) as event_source:
                    try:
                        async for message in event_source:
                            self.bot.loop.create_task(self.on_message(message))
                    except ConnectionError:
                        log.exception("Error with pixelcanvas event source!")
                    except Exception:
                        log.exception("Error with pixelcanvas event source!")
            except Exception:
                log.exception("Error with pixelcanvas event source!")

            self.last_failure = time()


class PxlsSpaceConnection(LongrunningWSConnection):
    def __init__(self, *args):
        super().__init__(*args)
        self.canvas = "pxlsspace"

        self.player_count = None

        self.last_failure = None
        self.failures = 0

    async def on_message(self, data):
        now = time()
        self.alive = now
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

                # Undo mechanism. Implement caching here too so we can track and emit for these properly.
                if color == -1:
                    return

                async with self.listener_lock:
                    for _, listener in self.listeners.items():
                        self.bot.loop.create_task(listener(x, y, color, "pxlsspace"))

        elif message["type"] == "users":
            count = int(message["count"])
            self.player_count = [now, count]
            self.update_online(now, count)

    async def run(self):
        await self.add_listener(self.update_placements)

        while True:
            if self.last_failure:
                failure_delta = time() - self.last_failure
                if failure_delta < 60 * 5:
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
