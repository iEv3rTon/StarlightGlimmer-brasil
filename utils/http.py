import asyncio
import io
import json
import logging
from time import time

import aiohttp
import requests
import websockets
from typing import Iterable

from objects.chunks import BigChunk, ChunkPz, PxlsBoard
from objects.errors import HttpCanvasError, HttpGeneralError, NoJpegsError, NotPngError, TemplateHttpError
from utils import version

log = logging.getLogger(__name__)

useragent = {
    'User-Agent': 'StarlightGlimmer/{} (+https://github.com/BrickGrass/StarlightGlimmer)'.format(version.VERSION),
    'Cache-Control': 'no-cache'
}


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

    async def check_cache(chunks):
        async with bot.pz.chunk_lock:
            for chunk in chunks:
                cached = bot.pz.chunks.get(f"{chunk.x}_{chunk.y}", None)
                if cached:
                    chunk._image = cached.to_image()
                    chunk.loaded = True

    async def get_chunks(chunks):
        for chunk in chunks:
            chunk.loaded = False

        await check_cache(chunks)

        if all(chunk.loaded for chunk in chunks):
            return

        chunks = [c for c in chunks if not c.loaded]

        for chunk in chunks:
            await bot.pz.chunk_queue.put([chunk.x, chunk.y])

        while True:
            await check_cache(chunks)

            if all(chunk.loaded for chunk in chunks):
                return
            await asyncio.sleep(0.1)

    try:
        await asyncio.wait_for(get_chunks(chunks), timeout=60)
    except asyncio.TimeoutError:
        raise HttpCanvasError('pixelzone')


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
            return data['result']['data']['online']


async def fetch_online_pixelzone(bot):
    """Returns the number of users who are currently online pixelzone, integer."""
    if bot.pz.player_count:
        return bot.pz.player_count
    else:
        raise HttpCanvasError('pixelzone')


async def fetch_online_pxlsspace(bot):
    """Returns the number of users who are currently online pxls.space, integer."""
    if bot.px.player_count:
        return bot.px.player_count
    else:
        raise HttpCanvasError('pxlsspace')


async def get_changelog(version):
    """Gets recent changelog data from my github page.

    Arguments:
    version - Version number, float.

    Returns:
    An iterable of all data from the changelog which matches the current version number.
    """
    url = "https://api.github.com/repos/BrickGrass/StarlightGlimmer/releases"
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise HttpGeneralError
            data = json.loads(await resp.read())
            return next((x for x in data if x['tag_name'] == "{}".format(version.VERSION)), None)


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
