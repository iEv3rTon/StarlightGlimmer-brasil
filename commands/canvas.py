import logging
import io
import math
import re
import requests
import aiohttp
from PIL import Image
import math

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
        iter_args = iter(args)
        a = next(iter_args, None)
        if a == "-e":
            list_pixels = True
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
                    = await render.diff(t.x, t.y, data, zoom, fetchers[t.canvas], colors.by_name[t.canvas])

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
                        out = '\n'.join(out)
                    if err > 15:
                        haste = []
                        for i, p in enumerate(err_list):
                            x, y, current, target = p
                            current = ctx.s("color.{}.{}".format(t.canvas, current))
                            target = ctx.s("color.{}.{}".format(t.canvas, target))
                            haste.append("({},{}) is {}, should be {}".format(x + t.x, y + t.y, current, target))
                            if i == 50:
                                haste.append("...")
                                break
                        """And here send the haste list to hastebin formatted correctly"""
                        r = requests.post('https://hastebin.com/documents', data = '\n'.join(haste))
                        """Catch 503 errors lol"""
                        if r.status_code == 503:
                            for x in range(16):
                                out.append(haste[x])
                            out.append("```")
                            out.append("**Hastebin returned a 503 error. :(**")
                            out = '\n'.join(out)
                        else:
                            """Capture the returned code and make out hastbin.com/<code>"""
                            out = "Errors: https://hastebin.com/" + str(r.content)[10:20]
                    await ctx.send(out)

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

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.group(name="dither", invoke_without_command=True)
    async def dither(self, ctx):
        await ctx.invoke_default("dither")

    @commands.cooldown(1, 5, BucketType.guild)
    @dither.command(name="pixelcanvas", aliases=["pc"])
    async def dither_pixelcanvas(self, ctx, url=None):
        await _dither(ctx, url, "pixelcanvas", colors.pcDitherColours, colors.pcClashes)

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
        iter_args = iter(args)
        a = next(iter_args, None)
        if a == "-e":
            list_pixels = True
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
        diff_img, tot, err, bad, err_list = await render.diff(x, y, data, zoom, fetch, palette)

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

#finds the average of two colours
def average(first, second):
    red = (first[0] + second[0]) / 2
    green = (first[1] + second[1]) / 2
    blue = (first[2] + second[2]) / 2
    return (red, green, blue)

#finds the distance between two colours
def distance(point, target):
    dx = point[0] - target[0]
    dy = point[1] - target[1]
    dz = point[2] - target[2]

    #uses pythagoras (getting the root isn't required)
    return dx**2 + dy**2 + dz**2

def closest(pixel, pallete):
    #haha, small D
    smallD = [9999999,{}]
    for colour in pallete:
        newD = distance(pixel, colour[0])

        if newD < smallD[0]:
            smallD = [newD,colour]
        #useful for white pixels
        if newD == 0:
            break

    return smallD[1]

def ditherGet(pixel, x, y, greyscaleDithers, dithList):
    #determines if a pixel is grey, and chooses a pallete accordingly
    if pixel[0] == pixel[1] and pixel[1] == pixel[2]:
        colour = closest(pixel, greyscaleDithers)
    else:
        colour = closest(pixel, dithList)

    #returns colour[1] if it's an even amount of pixels from the origin and colour[2] if it's odd, creates a hash pattern
    return colour[((x+y) % 2) + 1]

async def _dither(ctx, url, canvas, palette, clashes):
    url = await select_url(ctx, url)
    if url is None:
        await ctx.send("You must attach an image to dither.")
        return

    #load user's image
    try:
        with await http.get_template(url, "image") as data:
            with Image.open(data).convert("RGBA") as origImg:
                if origImg.height > 1500 or origImg.width > 1500:
                    return await ctx.send("Image is too big, under 1500x1500 only please.")

                #generates a list of possible dithers
                dithList = []
                for cOne in palette:
                    for cTwo in palette:
                        dither = average(palette[cOne],palette[cTwo])
                        #omits dither if it's already in the list or it clashes
                        if [dither,cTwo,cOne] in dithList or [cOne, cTwo] in clashes or [cTwo, cOne] in clashes:
                            pass
                        else:
                            dithList.append([dither,cOne,cTwo])

                #generates greyscale dithers from all possible ones
                greyscaleDithers = []
                for colour in dithList:
                    if colour[0][0] == colour[0][1] and colour[0][1] == colour[0][2]:
                        greyscaleDithers.append(colour)

                #generates a new image
                newImg = Image.new("RGBA", origImg.size, "white")
                pArray = newImg.load()

                message = await ctx.send("`Converting - 0%`")

                _25percent = math.floor(origImg.height * 0.25)
                _50percent = math.floor(origImg.height * 0.5)
                _75percent = math.floor(origImg.height * 0.75)

                #convert
                for y in range(origImg.height):
                    for x in range(origImg.width):
                        reduced = ditherGet(origImg.getpixel((x, y)), x, y, greyscaleDithers, dithList)
                        pArray[x, y] = palette[reduced]

                        if y == _25percent and x == 1:
                            await message.edit(content="`Converting - 25%`")
                        elif y == _50percent and x == 1:
                            await message.edit(content="`Converting - 50%`")
                        elif y == _75percent and x == 1:
                            await message.edit(content="`Converting - 75%`")

                with io.BytesIO() as bio:
                    newImg.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, "dithered.png")
                    await message.delete()
                    return await ctx.send(content="`Image dithered:`", file=f)


    except aiohttp.client_exceptions.InvalidURL:
        raise UrlError
    except IOError:
        raise PilImageError
