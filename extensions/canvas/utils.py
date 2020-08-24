import io
import re
import math
from struct import unpack_from
import time
import uuid
import logging

import aiohttp
import discord
import numpy as np
from PIL import Image, ImageChops
import websockets

from objects.database_models import session_scope
from objects.errors import UrlError, TemplateHttpError
from utils import GlimmerArgumentParser, http, canvases

log = logging.getLogger(__name__)


class Pixel:
    def __init__(self, current, target, x, y):
        self.current = current
        self.target = target
        self.x = x
        self.y = y


class CheckSource(discord.ext.menus.ListPageSource):
    def __init__(self, data):
        super().__init__(data, per_page=10)
        self.embed = None

    async def format_page(self, menu, entries):
        embed = discord.Embed(
            title=menu.ctx.s("canvas.template_report_header"),
            description=f"Page {menu.current_page + 1} of {self.get_max_pages()}")
        embed.set_footer(
            text="Scroll using the reactions below to see other pages.")

        offset = menu.current_page * self.per_page
        for i, template in enumerate(entries, start=offset):
            if i == offset + self.per_page:
                break
            embed.add_field(
                name=template.name,
                value="[{e}: {e_val}/{t_val} | {p}: {p_val}]({url})".format(
                    e=menu.ctx.s("bot.errors"),
                    e_val=template.errors,
                    t_val=template.size,
                    p=menu.ctx.s("bot.percent"),
                    p_val="{:>6.2f}%".format(100 * (template.size - template.errors) / template.size),
                    url=canvases.url_templates[template.canvas].format(template.x, template.y)
                    ),
                inline=False)
        self.embed = embed
        return embed


async def select_url(ctx, input_url):
    """Selects a url from the available information.

    Arguments:
    ctx - commands.Context object.
    input_url - A string containing a possible url, or None.

    Returns:
    Nothing or a discord url, string.
    """
    if input_url:
        if re.search(r'^(?:https?://)cdn\.discordapp\.com/', input_url):
            return input_url
        raise UrlError
    if len(ctx.message.attachments) > 0:
        return ctx.message.attachments[0].url


async def get_dither_image(url, ctx):
    """Fetches and opens an image as a bytestream

    Arguments:
    url - The url of the image to fetch, string.

    Returns:
    A bytestream of an image, or nothing.
    """
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise TemplateHttpError("Image")
            if resp.content_type != "image/png" and resp.content_type != "image/jpg" and resp.content_type != "image/jpeg":
                await ctx.send(ctx.s("canvas.dither_notpngorjpg"))
            return io.BytesIO(await resp.read())


def dither_argparse(ctx, args):
    parser = GlimmerArgumentParser(ctx)
    parser.add_argument(
        "-d", "--ditherType",
        choices=["b", "bayer", "y", "yliluoma", "fs", "floyd-steinberg"],
        default="bayer")
    parser.add_argument(
        "-t", "--threshold", type=int,
        choices=[2, 4, 8, 16, 32, 64, 128, 256, 512])
    parser.add_argument(
        "-o", "--order", type=int,
        choices=[2, 4, 8, 16])
    try:
        a = parser.parse_args(args)
    except TypeError:
        raise discord.ext.commands.BadArgument

    def default(value, default):
        return value if value is not None else default

    names = {
        "b": "bayer",
        "y": "yliluoma",
        "fs": "floyd-steinberg"
    }
    default_thresholds = {
        "bayer": 256,
    }
    default_orders = {
        "bayer": 4,
        "yliluoma": 8,
        "floyd-steinberg": 2
    }

    dither_type = default(names.get(a.ditherType, None), a.ditherType)
    dither_type = dither_type if dither_type is not None else "bayer"  # Incase they select an invalid option for this
    threshold = default(a.threshold, default_thresholds.get(dither_type))
    order = order = default(a.order, default_orders.get(dither_type))

    return dither_type, threshold, order


def process_check(templates, chunks):
    # We need to begin a new session for this thread and migrate all
    # the template objects over to it so we can use them safely!

    with session_scope() as session:
        ts = [session.merge(t) for t in templates]

        example_chunk = next(iter(chunks))
        for t in ts:
            empty_bcs, shape = example_chunk.get_intersecting(t.x, t.y, t.width, t.height)
            tmp = Image.new("RGBA", (example_chunk.width * shape[0], example_chunk.height * shape[1]))
            for i, ch in enumerate(empty_bcs):
                ch = next((x for x in chunks if x == ch))
                if ch.is_in_bounds():
                    tmp.paste(ch.image, ((i % shape[0]) * ch.width, (i // shape[0]) * ch.height))

            x, y = t.x - empty_bcs[0].p_x, t.y - empty_bcs[0].p_y
            tmp = tmp.crop((x, y, x + t.width, y + t.height))
            template = Image.open(http.get_template_blocking(t.url, t.name)).convert('RGBA')
            alpha = Image.new('RGBA', template.size, (255, 255, 255, 0))
            template = Image.composite(template, alpha, template)
            tmp = Image.composite(tmp, alpha, template)
            tmp = ImageChops.difference(tmp.convert('RGB'), template.convert('RGB'))
            t.errors = np.array(tmp).any(axis=-1).sum()

        return [{"tid": t.id, "errors": t.errors} for t in ts]
