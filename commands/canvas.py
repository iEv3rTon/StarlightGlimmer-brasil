import logging
import io
import math
import re
import requests
import aiohttp
from PIL import Image
import math

import datetime

import discord
from discord.ext import commands
from discord.ext.commands import BucketType

from objects.bot_objects import GlimContext
from objects.errors import FactionNotFoundError, IdempotentActionError
import utils
from utils import colors, http, render, sqlite as sql

log = logging.getLogger(__name__)


class Canvas(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =======================
    #          DIFF
    # =======================

    @commands.group(name="diff", invoke_without_command=True, aliases=["d"])
    async def diff(self, ctx, *args):
        if len(args) < 1:
            return
        list_pixels = False
        create_snapshot = False
        iter_args = iter(args)
        a = next(iter_args, None)
        if a == "-e":
            list_pixels = True
            a = next(iter_args, None)
        if a == "-s" or a == "--snapshot":
            create_snapshot = True
            a = next(iter_args, None)
        if a == "-f":
            fac = next(iter_args, None)
            if fac is None:
                await ctx.send(ctx.s("error.missing_arg_faction"))
                return
            f = sql.guild_get_by_faction_name_or_alias(fac)
            if not f:
                await ctx.send(ctx.s("error.faction_not_found"))
                return
            name = next(iter_args, None)
            zoom = next(iter_args, 1)
            t = sql.template_get_by_name(f.id, name)
        else:
            name = a
            zoom = next(iter_args, 1)
            t = sql.template_get_by_name(ctx.guild.id, name)

        try:
            if type(zoom) is not int:
                if zoom.startswith("#"):
                    zoom = zoom[1:]
                zoom = int(zoom)
        except ValueError:
            zoom = 1

        if t:
            async with ctx.typing():
                log.info("(T:{} | GID:{})".format(t.name, t.gid))
                data = await http.get_template(t.url, t.name)
                max_zoom = int(math.sqrt(4000000 // (t.width * t.height)))
                zoom = max(1, min(zoom, max_zoom))

                fetchers = {
                    'pixelcanvas': render.fetch_pixelcanvas,
                    'pixelzone': render.fetch_pixelzone,
                    'pxlsspace': render.fetch_pxlsspace
                }

                diff_img, tot, err, bad, err_list \
                    = await render.diff(t.x, t.y, data, zoom, fetchers[t.canvas], colors.by_name[t.canvas], create_snapshot)

                done = tot - err
                perc = done / tot
                if perc < 0.00005 and done > 0:
                    perc = ">0.00%"
                elif perc >= 0.99995 and err > 0:
                    perc = "<100.00%"
                else:
                    perc = "{:.2f}%".format(perc * 100)
                out = ctx.s("canvas.diff") if bad == 0 else ctx.s("canvas.diff_bad_color")
                out = out.format(done, tot, err, perc, bad=bad)

                with io.BytesIO() as bio:
                    diff_img.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, "diff.png")
                    await ctx.send(content=out, file=f)

                if list_pixels and len(err_list) > 0:
                    out = ["```xl"]
                    if len(err_list) <= 15:
                        for i, pixel in enumerate(err_list):
                            x, y, current, target = pixel
                            current = ctx.s("color.{}.{}".format(t.canvas, current))
                            target = ctx.s("color.{}.{}".format(t.canvas, target))
                            out.append("({}, {}) is {}, should be {}".format(x + t.x, y + t.y, current, target))
                            if i == 15:
                                break
                        out.append("```")
                        await ctx.send('\n'.join(out))
                        return
                    if len(err_list) > 15:
                        haste = []
                        for i, pixel in enumerate(err_list):
                            x, y, current, target = pixel
                            current = ctx.s("color.{}.{}".format(t.canvas, current))
                            target = ctx.s("color.{}.{}".format(t.canvas, target))
                            haste.append("({}, {}) is {}, should be {}".format(x + t.x, y + t.y, current, target))
                            if i == 50:
                                haste.append("...")
                                break
                        #And here send the haste list to hastebin formatted correctly
                        try:
                            r = requests.post('https://hastebin.com/documents', data = '\n'.join(haste), timeout=10)
                        except requests.exceptions.Timeout:
                            #Send as 15 as codeblock if timed out
                            for x in range(15):
                                out.append(haste[x])
                            out.append("...```**Hastebin returned an error.**")
                            await ctx.send('\n'.join(out))
                            return
                        if r.status == 200:
                            #Capture the returned code and make out hastbin.com/<code>
                            out = "Errors: https://hastebin.com/" + str(r.content)[10:20]
                            await ctx.send(out)
                            return
                        #Send as 15 as codeblock since the response code wasn't 200
                        for x in range(15):
                            out.append(haste[x])
                        out.append("...```**Hastebin returned an error.**")
                        await ctx.send('\n'.join(out))
                return
        await ctx.invoke_default("diff")

    @diff.command(name="pixelcanvas", aliases=["pc"])
    async def diff_pixelcanvas(self, ctx, *args):
        await _diff(ctx, args, "pixelcanvas", render.fetch_pixelcanvas, colors.pixelcanvas)

    @commands.cooldown(1, 5, BucketType.guild)
    @diff.command(name="pixelzone", aliases=["pz"])
    async def diff_pixelzone(self, ctx, *args):
        await _diff(ctx, args, "pixelzone", render.fetch_pixelzone, colors.pixelzone)

    @commands.cooldown(1, 5, BucketType.guild)
    @diff.command(name="pxlsspace", aliases=["ps"])
    async def diff_pxlsspace(self, ctx, *args):
        await _diff(ctx, args, "pxlsspace", render.fetch_pxlsspace, colors.pxlsspace)

    # =======================
    #        PREVIEW
    # =======================

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.group(name="preview", invoke_without_command=True, aliases=["p"])
    async def preview(self, ctx, *args):
        if len(args) < 1:
            return
        preview_template_region = False
        iter_args = iter(args)
        a = next(iter_args, None)
        if a == "-t":
            preview_template_region = True
            a = next(iter_args, None)
        if a == "-f":
            fac = next(iter_args, None)
            if fac is None:
                await ctx.send(ctx.s("error.missing_arg_faction"))
                return
            f = sql.guild_get_by_faction_name_or_alias(fac)
            if not f:
                await ctx.send(ctx.s("error.faction_not_found"))
                return
            name = next(iter_args, None)
            zoom = next(iter_args, 1)
            t = sql.template_get_by_name(f.id, name)
        else:
            name = a
            zoom = next(iter_args, 1)
            t = sql.template_get_by_name(ctx.guild.id, name)

        try:
            if type(zoom) is not int:
                if zoom.startswith("#"):
                    zoom = zoom[1:]
                zoom = int(zoom)
        except ValueError:
            zoom = 1

        if t:
            async with ctx.typing():
                log.info("(T:{} | GID:{})".format(t.name, t.gid))
                max_zoom = int(math.sqrt(4000000 // (t.width * t.height)))
                zoom = max(-8, min(zoom, max_zoom))

                fetchers = {
                    'pixelcanvas': render.fetch_pixelcanvas,
                    'pixelzone': render.fetch_pixelzone,
                    'pxlsspace': render.fetch_pxlsspace
                }

                if preview_template_region:
                    preview_img = await render.preview(*t.center(), zoom, fetchers[t.canvas])
                else:
                    preview_img = await render.preview_template(t, zoom, fetchers[t.canvas])

                with io.BytesIO() as bio:
                    preview_img.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, "preview.png")
                    await ctx.send(file=f)

                return
        await ctx.invoke_default("preview")

    @commands.cooldown(1, 5, BucketType.guild)
    @preview.command(name="pixelcanvas", aliases=["pc"])
    async def preview_pixelcanvas(self, ctx, *args):
        await _preview(ctx, args, render.fetch_pixelcanvas)

    @commands.cooldown(1, 5, BucketType.guild)
    @preview.command(name="pixelzone", aliases=["pz"])
    async def preview_pixelzone(self, ctx, *args):
        await _preview(ctx, args, render.fetch_pixelzone)

    @commands.cooldown(1, 5, BucketType.guild)
    @preview.command(name="pxlsspace", aliases=["ps"])
    async def preview_pxlsspace(self, ctx, *args):
        await _preview(ctx, args, render.fetch_pxlsspace)

    # =======================
    #        QUANTIZE
    # =======================

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.group(name="quantize", invoke_without_command=True, aliases=["q"])
    async def quantize(self, ctx):
        await ctx.invoke_default("quantize")

    @commands.cooldown(1, 5, BucketType.guild)
    @quantize.command(name="pixelcanvas", aliases=["pc"])
    async def quantize_pixelcanvas(self, ctx, *args):
        await _quantize(ctx, args, "pixelcanvas", colors.pixelcanvas)

    @commands.cooldown(1, 5, BucketType.guild)
    @quantize.command(name="pixelzone", aliases=["pz"])
    async def quantize_pixelzone(self, ctx, *args):
        await _quantize(ctx, args, "pixelzone", colors.pixelzone)

    @commands.cooldown(1, 5, BucketType.guild)
    @quantize.command(name="pxlsspace", aliases=["ps"])
    async def quantize_pxlsspace(self, ctx, *args):
        await _quantize(ctx, args, "pxlsspace", colors.pxlsspace)

    # =======================
    #         DITHER
    # =======================

    @commands.cooldown(1, 30, BucketType.guild)
    @commands.group(name="dither", invoke_without_command=True)
    async def dither(self, ctx):
        await ctx.invoke_default("dither")

    @commands.cooldown(1, 30, BucketType.guild)
    @dither.command(name="geo32", aliases=["geo"])
    async def dither_geo32(self, ctx, *args):
        url = None
        iter_args = iter(args)
        arg = next(iter_args, None)
        if arg == "-b" or arg == "--bayer":
            threshold = next(iter_args, 256)
            try:
                threshold = int(threshold)
            except ValueError:
                await ctx.send("The threshold must be a positive integer.")
                return
            order = next(iter_args, 4)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            bayer_options = (threshold, order)
            await _dither(ctx, url, colors.geo32, "bayer", bayer_options)
            return
        if arg == "-y" or arg == "--yliluoma":
            order = next(iter_args, 8)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            await _dither(ctx, url, colors.geo32, "yliluoma", order)
            return
        if arg == "-f" or arg == "-fs" or arg == "--floyd-steinberg":
            order = next(iter_args, 2)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            await _dither(ctx, url, colors.geo32, "floyd-steinberg", order)
            return
        await ctx.send("Please specify what kind of dither to use")

    @commands.cooldown(1, 30, BucketType.guild)
    @dither.command(name="pixelcanvas", aliases=["pc"])
    async def dither_pixelcanvas(self, ctx, *args):
        url = None
        iter_args = iter(args)
        arg = next(iter_args, None)
        if arg == "-b" or arg == "--bayer":
            threshold = next(iter_args, 256)
            try:
                threshold = int(threshold)
            except ValueError:
                await ctx.send("The threshold must be a positive integer.")
                return
            order = next(iter_args, 4)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            bayer_options = (threshold, order)
            await _dither(ctx, url, colors.pixelcanvas, "bayer", bayer_options)
            return
        if arg == "-y" or arg == "--yliluoma":
            order = next(iter_args, 8)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            await _dither(ctx, url, colors.pixelcanvas, "yliluoma", order)
            return
        if arg == "-f" or arg == "-fs" or arg == "--floyd-steinberg":
            order = next(iter_args, 2)
            try:
                order = int(order)
            except ValueError:
                await ctx.send("The order must be a positive integer.")
                return
            await _dither(ctx, url, colors.pixelcanvas, "floyd-steinberg", order)
            return
        await ctx.send("Please specify what kind of dither to use")

    # =======================
    #         GRIDIFY
    # =======================

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.command(name="gridify", aliases=["g"])
    async def gridify(self, ctx, *args):
        faction = None
        color = 0x808080
        iter_args = iter(args)
        name = next(iter_args, None)
        if name == "-f":
            fac = next(iter_args, None)
            if fac is None:
                await ctx.send(ctx.s("error.missing_arg_faction"))
                return
            faction = sql.guild_get_by_faction_name_or_alias(fac)
            if not faction:
                await ctx.send(ctx.s("error.faction_not_found"))
                return
            name = next(iter_args, None)
        if name == "-c":
            try:
                color = abs(int(next(iter_args, None), 16) % 0xFFFFFF)
                name = next(iter_args, None)
            except ValueError:
                await ctx.send(ctx.s("error.invalid_color"))
                return

        def parse_zoom(z):
            try:
                if type(z) is int:
                    return z
                if type(z) is str:
                    if z.startswith("#"):
                        z = z[1:]
                    return int(z)
                if z is None:
                    return 8
            except ValueError:
                return 8

        t = sql.template_get_by_name(faction.id, name) if faction else sql.template_get_by_name(ctx.guild.id, name)
        if t:
            log.info("(T:{} | GID:{})".format(t.name, t.gid))
            data = await http.get_template(t.url, t.name)
            max_zoom = int(math.sqrt(4000000 // (t.width * t.height)))
            zoom = max(1, min(parse_zoom(next(iter_args, 1)), max_zoom))
            template = await render.gridify(data, color, zoom)
        else:
            att = await utils.verify_attachment(ctx)
            data = io.BytesIO()
            await att.save(data)
            max_zoom = int(math.sqrt(4000000 // (att.width * att.height)))
            zoom = max(1, min(parse_zoom(name), max_zoom))
            template = await render.gridify(data, color, zoom)

        with io.BytesIO() as bio:
            template.save(bio, format="PNG")
            bio.seek(0)
            f = discord.File(bio, "gridded.png")
            await ctx.send(file=f)

    # ======================
    #       DITHERCHART
    # ======================

    @commands.group(name="ditherchart", invoke_without_command=True)
    async def ditherchart(self, ctx):
        await ctx.invoke_default("ditherchart")

    @ditherchart.command(name="pixelcanvas", aliases=["pc"])
    async def ditherchart_pixelcanvas(self, ctx):
        await ctx.send(file=discord.File("assets/dither_chart_pixelcanvas.png", "dither_chart_pixelcanvas.png"))

    @ditherchart.command(name="pixelzone", aliases=["pz"])
    async def ditherchart_pixelzone(self, ctx):
        await ctx.send(file=discord.File("assets/dither_chart_pixelzone.png", "dither_chart_pixelzone.png"))

    @ditherchart.command(name="pxlsspace", aliases=["ps"])
    async def ditherchart_pxlsspace(self, ctx):
        await ctx.send(file=discord.File("assets/dither_chart_pxlsspace.png", "dither_chart_pxlsspace.png"))

    # ======================
    #         REPEAT
    # ======================

    @commands.command(name="repeat", aliases=["r"])
    async def repeat(self, ctx):
        async for msg in ctx.history(limit=50, before=ctx.message):
            new_ctx = await self.bot.get_context(msg, cls=GlimContext)
            new_ctx.is_repeat = True

            match = re.match('^{}(diff|d|preview|p)'.format(ctx.prefix), msg.content)
            if match:
                await self.bot.invoke(new_ctx)
                return

            if await utils.autoscan(new_ctx):
                return
        await ctx.send(ctx.s("canvas.repeat_not_found"))

    # ======================
    #         ONLINE
    # ======================

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.group(name="online", aliases=["o"], invoke_without_command=True)
    async def online(self, ctx):
        await ctx.invoke_default("online")

    @online.command(name="pixelcanvas", aliases=["pc"])
    async def online_pixelcanvas(self, ctx):
        ct = await http.fetch_online_pixelcanvas()
        await ctx.send(ctx.s("canvas.online").format(ct, "Pixelcanvas"))

    @online.command(name="pixelzone", aliases=["pz"])
    async def online_pixelzone(self, ctx):
        async with ctx.typing():
            msg = await ctx.send(ctx.s("canvas.online_await"))
            ct = await http.fetch_online_pixelzone()
            await msg.edit(content=ctx.s("canvas.online").format(ct, "Pixelzone"))

    @online.command(name="pxlsspace", aliases=["ps"])
    async def online_pxlsspace(self, ctx):
        async with ctx.typing():
            msg = await ctx.send(ctx.s("canvas.online_await"))
            ct = await http.fetch_online_pxlsspace()
            await msg.edit(content=ctx.s("canvas.online").format(ct, "Pxls.space"))


async def _diff(ctx, args, canvas, fetch, palette):
    async with ctx.typing():
        att = await utils.verify_attachment(ctx)
        list_pixels = False
        create_snapshot = False
        iter_args = iter(args)
        a = next(iter_args, None)
        if a == "-e":
            list_pixels = True
            a = next(iter_args, None)
        if a == "-s" or a == "--snapshot":
            create_snapshot = True
            a = next(iter_args, None)
        if a and ',' in a:
            x, y = a.split(',')
        else:
            x = a
            y = next(iter_args, None)

        try:
            x = int(x)
            y = int(y)
        except (ValueError, TypeError):
            await ctx.send(ctx.s("canvas.invalid_input"))
            return

        zoom = next(iter_args, 1)
        try:
            if type(zoom) is not int:
                if zoom.startswith("#"):
                    zoom = zoom[1:]
                zoom = int(zoom)
        except ValueError:
            zoom = 1

        data = io.BytesIO()
        await att.save(data)
        max_zoom = int(math.sqrt(4000000 // (att.width * att.height)))
        zoom = max(1, min(zoom, max_zoom))
        diff_img, tot, err, bad, err_list = await render.diff(x, y, data, zoom, fetch, palette, create_snapshot)

        done = tot - err
        perc = done / tot
        if perc < 0.00005 and done > 0:
            perc = ">0.00%"
        elif perc >= 0.99995 and err > 0:
            perc = "<100.00%"
        else:
            perc = "{:.2f}%".format(perc * 100)
        out = ctx.s("canvas.diff") if bad == 0 else ctx.s("canvas.diff_bad_color")
        out = out.format(done, tot, err, perc, bad=bad)

        with io.BytesIO() as bio:
            diff_img.save(bio, format="PNG")
            bio.seek(0)
            f = discord.File(bio, "diff.png")
            await ctx.send(content=out, file=f)

        if list_pixels and len(err_list) > 0:
            out = ["```xl"]
            if err <= 15:
                for p in err_list:
                    x, y, current, target = p
                    current = ctx.s("color.{}.{}".format(t.canvas, current))
                    target = ctx.s("color.{}.{}".format(t.canvas, target))
                    out.append("({},{}) is {}, should be {}".format(x + t.x, y + t.y, current, target))
                    if err == 15:
                        break
                out.append("```")
            if err > 15:
                haste = []
                for p in err_list:
                    x, y, current, target = p
                    current = ctx.s("color.{}.{}".format(t.canvas, current))
                    target = ctx.s("color.{}.{}".format(t.canvas, target))
                    haste.append("({},{}) is {}, should be {}".format(x + t.x, y + t.y, current, target))
                    if err == 50:
                        haste.append("...")
                        break
                """And here send the haste list to hastebin formatted correctly"""
                r = requests.post('https://hastebin.com/documents', data = '\n'.join(haste))
                """Capture the returned code and make out hastbin.com/<code>"""
                out = str(r.content)
            await ctx.send('\n'.join(out))

async def _preview(ctx, args, fetch):
    async with ctx.typing():
        iter_args = iter(args)
        a = next(iter_args, None)
        if a and ',' in a:
            x, y = a.split(',')
        else:
            x = a
            y = next(iter_args, None)

        try:
            x = int(x)
            y = int(y)
        except (ValueError, TypeError):
            await ctx.send(ctx.s("canvas.invalid_input"))
            return

        zoom = next(iter_args, 1)
        try:
            if type(zoom) is not int:
                if zoom.startswith("#"):
                    zoom = zoom[1:]
                zoom = int(zoom)
        except ValueError:
            zoom = 1
        zoom = max(min(zoom, 16), -8)

        preview_img = await render.preview(x, y, zoom, fetch)

        with io.BytesIO() as bio:
            preview_img.save(bio, format="PNG")
            bio.seek(0)
            f = discord.File(bio, "preview.png")
            await ctx.send(file=f)


async def _quantize(ctx, args, canvas, palette):
    gid = ctx.guild.id
    iter_args = iter(args)
    name = next(iter_args, None)
    if name == "-f":
        fac = next(iter_args, None)
        if fac is None:
            await ctx.send(ctx.s("error.missing_arg_faction"))
            return
        faction = sql.guild_get_by_faction_name_or_alias(fac)
        if not faction:
            raise FactionNotFoundError
        gid = faction.id
        name = next(iter_args, None)
    t = sql.template_get_by_name(gid, name)

    data = None
    if t:
        log.info("(T:{} | GID:{})".format(t.name, t.gid))
        if t.canvas == canvas:
            raise IdempotentActionError
        data = await http.get_template(t.url, t.name)
    else:
        att = await utils.verify_attachment(ctx)
        if att:
            data = io.BytesIO()
            await att.save(data)

    if data:
        template, bad_pixels = await render.quantize(data, palette)

        with io.BytesIO() as bio:
            template.save(bio, format="PNG")
            bio.seek(0)
            f = discord.File(bio, "template.png")
            return await ctx.send(ctx.s("canvas.quantize").format(bad_pixels), file=f)

async def select_url(ctx, input_url):
    if input_url:
        if re.search('^(?:https?://)cdn\.discordapp\.com/', input_url):
            return input_url
        raise UrlError
    if len(ctx.message.attachments) > 0:
        return ctx.message.attachments[0].url

async def get_dither_image(url, name):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            if resp.status != 200:
                raise TemplateHttpError(name)
            if resp.content_type != "image/png" and resp.content_type != "image/jpg" and resp.content_type != "image/jpeg":
                await ctx.send("Only png's or jpg's can be dithered.")
            return io.BytesIO(await resp.read())

async def _dither(ctx, url, palette, type, options):
    start_time = datetime.datetime.now()

    with ctx.typing():
        #getting the attachment url
        url = await select_url(ctx, url)
        if url is None:
            await ctx.send("You must attach an image to dither.")
            return

        #load user's image
        try:
            with await get_dither_image(url, "image") as data:
                with Image.open(data).convert("RGBA") as origImg:
                    dithered_image = None
                    option_string = ""

                    if type == "bayer":
                        if origImg.height > 1500 or origImg.width > 1500:
                            return await ctx.send("Image is too big, under 1500x1500 only please.")
                        threshold = options[0]
                        order = options[1]
                        valid_thresholds = [2, 4, 8, 16, 32, 64, 128, 256, 512]
                        valid_orders = [2, 4, 8, 16]
                        if threshold in valid_thresholds and order in valid_orders:
                            dithered_image = await render.bayer_dither(origImg, palette, threshold, order)
                            option_string = "Threshold: {}/4 Order: {}".format(threshold, order)
                        else:
                            # threshold or order val provided is not valid
                            await ctx.send("Threshold or order value provided is not valid.")
                            return
                    elif type == "yliluoma":
                        if origImg.height > 100 or origImg.width > 100:
                            return await ctx.send("Image is too big, under 100x100 only please.")
                        order = options
                        valid_orders = [2, 4, 8, 16]
                        if order in valid_orders:
                            dithered_image = await render.yliluoma_dither(origImg, palette, order)
                            option_string = "Order: {}".format(order)
                        else:
                            # order val provided is not valid
                            await ctx.send("Order value provided is not valid.")
                            return
                    elif type == "floyd-steinberg":
                        if origImg.height > 100 or origImg.width > 100:
                            return await ctx.send("Image is too big, under 100x100 only please.")
                        order = options
                        valid_orders = [2, 4, 8, 16]
                        if order in valid_orders:
                            dithered_image = await render.floyd_steinberg_dither(origImg, palette, order)
                            option_string = "Order: {}".format(order)
                        else:
                            # order val provided is not valid
                            await ctx.send("Order value provided is not valid.")
                            return

                    with io.BytesIO() as bio:
                        dithered_image.save(bio, format="PNG")
                        bio.seek(0)
                        f = discord.File(bio, "dithered.png")

                        end_time = datetime.datetime.now()
                        duration = (end_time - start_time).total_seconds()

                        return await ctx.send(
        content="`Image dithered in {:.2f} seconds with {} dithering. {}`".format(duration, type, option_string), file=f)

        except aiohttp.client_exceptions.InvalidURL:
            raise UrlError
        except IOError:
            raise PilImageError
