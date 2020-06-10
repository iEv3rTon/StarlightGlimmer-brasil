import datetime
from functools import partial
import io
import itertools
import logging
import math
import re

import aiohttp
import discord
from discord.ext import commands, menus
from discord.ext.commands import BucketType, BadArgument
import numpy as np
from PIL import Image, ImageChops

from objects.bot_objects import GlimContext
from objects.chunks import BigChunk, ChunkPz, PxlsBoard
from objects.errors import IdempotentActionError, NoTemplatesError, TemplateNotFoundError, TemplateHttpError, UrlError, PilImageError, TemplateTooLargeError
from objects.checker import Pixel, Checker
from utils import autoscan, colors, http, canvases, render, GlimmerArgumentParser, FactionAction, ColorAction, verify_attachment, sqlite as sql

log = logging.getLogger(__name__)


class Canvas(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =======================
    #          DIFF
    # =======================

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(
        name="diff",
        invoke_without_command=True,
        aliases=["d"],
        case_insensitive=True)
    async def diff(self, ctx, *args):
        log.info(f"g!diff run in {ctx.guild.name} with args: {args}")

        # Order Parsing
        try:
            name = args[0]
        except IndexError:
            await ctx.send("Error: no arguments were provided.")
            return

        if re.match(r"-\D+", name) is not None:
            name = args[-1]
            args = args[:-1]
        else:
            args = args[1:]

        if re.match(r"-{0,1}\d+", name) is not None:  # Skip to coords + image parsing
            await ctx.invoke_default("diff")
            return

        await self._pre_diff(ctx, args, name=name)

    @diff.command(name="pixelcanvas", aliases=["pc"])
    async def diff_pixelcanvas(self, ctx, *args):
        await self._pre_diff(ctx, args, canvas="pixelcanvas", fetch=render.fetch_pixelcanvas, palette=colors.pixelcanvas)

    @diff.command(name="pixelzone", aliases=["pz"])
    async def diff_pixelzone(self, ctx, *args):
        await self._pre_diff(ctx, args, canvas="pixelzone", fetch=render.fetch_pixelzone, palette=colors.pixelzone)

    @diff.command(name="pxlsspace", aliases=["ps"])
    async def diff_pxlsspace(self, ctx, *args):
        await self._pre_diff(ctx, args, canvas="pxlsspace", fetch=render.fetch_pxlsspace, palette=colors.pxlsspace)

    # =======================
    #        PREVIEW
    # =======================

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(
        name="preview",
        invoke_without_command=True,
        aliases=["p"],
        case_insensitive=True)
    async def preview(self, ctx, *args):
        log.info(f"g!preview run in {ctx.guild.name} with args: {args}")

        # Order Parsing
        try:
            name = args[0]
        except IndexError:
            await ctx.send("Error: no arguments were provided.")
            return

        if re.match(r"-\D+", name) is not None:
            name = args[-1]
            args = args[:-1]
        else:
            args = args[1:]

        if re.match(r"-{0,1}\d+", name) is not None:  # Skip to coords + image parsing
            await ctx.invoke_default("preview")
            return

        await self._preview(ctx, args, name=name)

    @preview.command(name="pixelcanvas", aliases=["pc"])
    async def preview_pixelcanvas(self, ctx, *args):
        await self._preview(ctx, args, fetch=render.fetch_pixelcanvas)

    @preview.command(name="pixelzone", aliases=["pz"])
    async def preview_pixelzone(self, ctx, *args):
        await self._preview(ctx, args, fetch=render.fetch_pixelzone)

    @preview.command(name="pxlsspace", aliases=["ps"])
    async def preview_pxlsspace(self, ctx, *args):
        await self._preview(ctx, args, fetch=render.fetch_pxlsspace)

    # =======================
    #        QUANTIZE
    # =======================

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(
        name="quantize",
        invoke_without_command=True,
        aliases=["q"],
        case_insensitive=True)
    async def quantize(self, ctx):
        await ctx.invoke_default("quantize")

    @quantize.command(name="pixelcanvas", aliases=["pc"])
    async def quantize_pixelcanvas(self, ctx, *args):
        await self._quantize(ctx, args, "pixelcanvas", colors.pixelcanvas)

    @quantize.command(name="pixelzone", aliases=["pz"])
    async def quantize_pixelzone(self, ctx, *args):
        await self._quantize(ctx, args, "pixelzone", colors.pixelzone)

    @quantize.command(name="pxlsspace", aliases=["ps"])
    async def quantize_pxlsspace(self, ctx, *args):
        await self._quantize(ctx, args, "pxlsspace", colors.pxlsspace)

    # =======================
    #         DITHER
    # =======================

    @commands.max_concurrency(2, per=BucketType.default)
    @commands.group(
        name="dither",
        invoke_without_command=True,
        case_insensitive=True)
    async def dither(self, ctx):
        await ctx.invoke_default("dither")

    @dither.command(name="geo32")
    async def dither_geo32(self, ctx, *args):
        dither_type, threshold, order = dither_argparse(ctx, args)
        await self._dither(ctx, colors.geo32, dither_type, threshold, order)

    @dither.command(name="pixelcanvas", aliases=["pc"])
    async def dither_pixelcanvas(self, ctx, *args):
        dither_type, threshold, order = dither_argparse(ctx, args)
        await self._dither(ctx, colors.pixelcanvas, dither_type, threshold, order)

    # =======================
    #          CHECK
    # =======================

    @commands.guild_only()
    @commands.cooldown(1, 10, BucketType.guild)
    @commands.command(name='check', aliases=['c'])
    async def check(self, ctx, *args):

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-e", "--onlyErrors", action='store_true')
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        parser.add_argument("-s", "--sort", default="name_az", choices=[
            "name_az", "name_za", "errors_az", "errors_za", "percent_az", "percent_za"])
        parser.add_argument("-p", "--page", default=1, type=int)
        try:
            a = parser.parse_args(args)
        except TypeError:
            return

        if a.faction:
            templates = sql.template_get_all_by_guild_id(a.faction.id)
        else:
            templates = sql.template_get_all_by_guild_id(ctx.guild.id)

        if len(templates) < 1:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(False)

        msg = None

        # Calc info + send temp msg
        for canvas, canvas_ts in itertools.groupby(templates, lambda tx: tx.canvas):
            ct = list(canvas_ts)
            msg = await self.check_canvas(ctx, ct, canvas, msg=msg)

        # Delete temp msg and send final report
        await msg.delete()

        ts = [t for t in templates if t.errors != 0] if a.onlyErrors else templates

        if a.sort == "name_az" or a.sort == "name_za":
            ts = sorted(ts, key=lambda t: t.name, reverse=(a.sort == "name_za"))
        elif a.sort == "errors_az" or a.sort == "errors_za":
            ts = sorted(ts, key=lambda t: t.errors, reverse=(a.sort == "errors_za"))
        elif a.sort == "percent_az" or a.sort == "percent_za":
            ts = sorted(ts, key=lambda t: (t.size - t.errors) / t.size, reverse=(a.sort == "percent_za"))

        ts = sorted(ts, key=lambda t: t.canvas)

        check_menu = menus.MenuPages(
            source=CheckSource(ts),
            clear_reactions_after=True,
            timeout=300.0)
        check_menu.current_page = max(min(a.page - 1, check_menu.source.get_max_pages()), 0)
        await check_menu.start(ctx, wait=True)
        check_menu.source.embed.set_footer(text=ctx.s("bot.timeout"))
        await check_menu.message.edit(embed=check_menu.source.embed)

    # =======================
    #         GRIDIFY
    # =======================

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.command(name="gridify", aliases=["g"])
    async def gridify(self, ctx, *args):
        log.info(f"g!gridify run in {ctx.guild.name} with args: {args}")

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        parser.add_argument("-c", "--color", default=0x808080, action=ColorAction)
        parser.add_argument("-z", "--zoom", type=int, default=1)

        # Pre-Parsing
        if len(args) == 0:
            name = None
            a = args
        elif args[0][0] != "-":
            name = args[0]
            a = args[1:]
        else:
            name = None
            a = args

        try:
            a = parser.parse_args(a)
        except TypeError:
            return

        gid = ctx.guild.id if not a.faction else a.faction.id
        t = sql.template_get_by_name(gid, name)

        if name:
            if t:
                log.info("(T:{} | GID:{})".format(t.name, t.gid))
                data = await http.get_template(t.url, t.name)
                max_zoom = int(math.sqrt(4000000 // (t.width * t.height)))
                zoom = max(1, min(a.zoom, max_zoom))
                template = render.gridify(data, a.color, zoom)
            else:
                raise TemplateNotFoundError(gid, name)
        else:
            att = await verify_attachment(ctx)
            data = io.BytesIO()
            await att.save(data)
            max_zoom = int(math.sqrt(4000000 // (att.width * att.height)))
            zoom = max(1, min(a.zoom, max_zoom))
            template = render.gridify(data, a.color, zoom)

        with io.BytesIO() as bio:
            template.save(bio, format="PNG")
            bio.seek(0)
            f = discord.File(bio, "gridded.png")
            await ctx.send(file=f)

    # ======================
    #       DITHERCHART
    # ======================

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(
        name="ditherchart",
        invoke_without_command=True,
        case_insensitive=True)
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

    @commands.cooldown(2, 5, BucketType.guild)
    @commands.command(name="repeat", aliases=["r"])
    async def repeat(self, ctx):
        async for msg in ctx.history(limit=50, before=ctx.message):
            new_ctx = await self.bot.get_context(msg, cls=GlimContext)
            new_ctx.is_repeat = True

            match = re.match('^{}(diff|d|preview|p)'.format(ctx.prefix), msg.content)
            if match:
                await new_ctx.reinvoke()
                return

            if await autoscan(new_ctx):
                return
        await ctx.send(ctx.s("canvas.repeat_not_found"))

    # ======================
    #         ONLINE
    # ======================

    @commands.cooldown(1, 5, BucketType.guild)
    @commands.group(
        name="online",
        aliases=["o"],
        invoke_without_command=True,
        case_insensitive=True)
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

    # ======================
    #        METHODS
    # ======================

    async def check_canvas(self, ctx, templates, canvas, msg=None):
        """Update the current total errors for a list of templates.

        Arguments:
        ctx - commands.Context object.
        templates - A list of template objects.
        canvas - The canvas that the above templates are on, string.
        msg - A discord.Message object, or nothing. This object is used to continually edit the same message.

        Returns:
        A discord.Message object.
        """
        chunk_classes = {
            'pixelcanvas': BigChunk,
            'pixelzone': ChunkPz,
            'pxlsspace': PxlsBoard
        }

        # Find all chunks that have templates on them
        chunks = set()
        for t in templates:
            empty_bcs, _shape = chunk_classes[canvas].get_intersecting(t.x, t.y, t.width, t.height)
            chunks.update(empty_bcs)

        if msg is not None:
            await msg.edit(content=ctx.s("canvas.fetching_data").format(canvases.pretty_print[canvas]))
        else:
            msg = await ctx.send(ctx.s("canvas.fetching_data").format(canvases.pretty_print[canvas]))
        await http.fetch_chunks(chunks)

        await msg.edit(content=ctx.s("canvas.calculating"))
        func = partial(process_check, templates, chunks)
        await self.bot.loop.run_in_executor(None, func)

        return msg

    async def _pre_diff(self, ctx, args, name=None, canvas=None, fetch=None, palette=None):
        if not name:
            att = await verify_attachment(ctx)

            # Order Parsing
            try:
                x, y = args[0], args[1]
            except IndexError:
                await ctx.send("Error: not enough arguments were provided.")
                return

            if re.match(r"-\D+", x) is not None:
                x, y = args[-2], args[-1]
                args = args[:-2]
            else:
                args = args[2:]

            # X and Y Cleanup
            try:
                # cleans up x and y by removing all spaces and chars that aren't 0-9 or the minus sign using regex. Then makes em ints
                x = int(re.sub('[^0-9-]', '', x))
                y = int(re.sub('[^0-9-]', '', y))
            except ValueError:
                await ctx.send(ctx.s("canvas.invalid_input"))
                return

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-e", "--errors", action='store_true')
        parser.add_argument("-s", "--snapshot", action='store_true')
        parser.add_argument("-c", "--highlightCorrect", action='store_true')
        parser.add_argument("-cb", "--colorBlind", action='store_true')
        parser.add_argument("-z", "--zoom", type=int, default=1)
        parser.add_argument("-t", "--excludeTarget", action='store_true')
        colorFilters = parser.add_mutually_exclusive_group()
        colorFilters.add_argument("-ec", "--excludeColors", nargs="+", type=int, default=None)
        colorFilters.add_argument("-oc", "--onlyColors", nargs="+", type=int, default=None)

        if name:
            parser.add_argument("-f", "--faction", default=None, action=FactionAction)

        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        if name:
            gid = ctx.guild.id if not args.faction else args.faction.id
            t = sql.template_get_by_name(gid, name)
            if t:
                data = await http.get_template(t.url, t.name)
                await self._diff(
                    ctx, args, t.x, t.y, t.width, t.height,
                    t.canvas, self.bot.fetchers[t.canvas],
                    colors.by_name[t.canvas], data)
            else:
                raise TemplateNotFoundError(gid, name)
        else:
            data = io.BytesIO()
            await att.save(data)
            await self._diff(
                ctx, args, x, y, att.width, att.height,
                canvas, fetch, palette, data)

    async def _diff(self, ctx, args, x, y, w, h, canvas, fetch, palette, data):
        """Sends a diff.

        Arguments:
        ctx - commands.Context object.
        args - A namespace object from argparse.
        x - X coord.
        y - Y coord.
        w - Width.
        h - Height.
        canvas - The name of the canvas to look at, string.
        fetch - The fetch function to use, points to a fetch function from render.py.
        palette - The palette in use on this canvas, a list of rgb tuples.
        data - An io.BytesIO object containing the image to diff.
        """
        async with ctx.typing():
            max_zoom = int(math.sqrt(4000000 // (w * h)))
            zoom = max(1, min(args.zoom, max_zoom))
            img = await fetch(x, y, w, h)
            func = partial(
                render.diff, x, y,
                data, zoom, img, palette,
                create_snapshot=args.snapshot,
                highlight_correct=args.highlightCorrect,
                color_blind=args.colorBlind)
            diff_img, tot, err, bad, err_list, bad_list \
                = await self.bot.loop.run_in_executor(None, func)

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

            if bad_list != []:
                bad_out = [ctx.s("canvas.diff_bad_color_list").format(num, *color) for color, num in bad_list]
                bad_out = "{0}{1}".format("\n".join(bad_out[:10]), "\n..." if len(bad_out) > 10 else "")
                embed = discord.Embed(color=discord.Color.from_rgb(*bad_list[0][0]))
                embed.add_field(name=ctx.s("canvas.diff_bad_color_title"), value=bad_out)

            with io.BytesIO() as bio:
                diff_img.save(bio, format="PNG")
                bio.seek(0)
                f = discord.File(bio, "diff.png")
                try:
                    await ctx.send(content=out, file=f, embed=embed)
                except UnboundLocalError:
                    await ctx.send(content=out, file=f)

            if args.errors and len(err_list) > 0:
                error_list = []
                for _x, _y, current, target in err_list:
                    # Color Filtering
                    c = current if not args.excludeTarget else target
                    if args.excludeColors:
                        if c in args.excludeColors:
                            continue
                    elif args.onlyColors:
                        if c not in args.onlyColors:
                            continue

                    # The current x,y are in terms of the template area, add to template start coords so they're in terms of canvas
                    _x += x
                    _y += y
                    error_list.append(Pixel(current, target, _x, _y))

                checker = Checker(self.bot, ctx, canvas, error_list)
                checker.connect_websocket()

    async def _dither(self, ctx, palette, type, threshold, order):
        """Sends a message containing a dithered version of the image given.

        Arguments:
        ctx - A commands.Context object.
        palette - The palette to be used, a list of rgb tuples.
        type - The dithering algorithm to use, string.
        threshold - Option for the dithering algorithms
        order - Option for the dithering algorithms

        Returns:
        The discord.Message object returned from ctx.send().
        """
        start_time = datetime.datetime.now()

        def too_large(img, limit):
            if img.height > limit or img.width > limit:
                raise TemplateTooLargeError(limit)

        async with ctx.typing():
            url = await select_url(ctx, None)
            if url is None:
                await ctx.send(ctx.s("error.no_attachment"))
                return

            try:
                with await get_dither_image(url, ctx) as data:
                    with Image.open(data).convert("RGBA") as origImg:
                        dithered_image = None
                        option_string = ""

                        if type == "bayer":
                            too_large(origImg, 1500)
                            option_string = ctx.s("canvas.dither_order_and_threshold_option").format(threshold, order)
                        elif type == "yliluoma" or type == "floyd-steinberg":
                            too_large(origImg, 200)
                            option_string = ctx.s("canvas.dither_order_option").format(order)

                        func = partial(
                            render.dither, origImg, palette, type=type,
                            threshold=threshold, order=order)
                        dithered_image = await self.bot.loop.run_in_executor(None, func)

                        with io.BytesIO() as bio:
                            dithered_image.save(bio, format="PNG")
                            bio.seek(0)
                            f = discord.File(bio, "dithered.png")

                            end_time = datetime.datetime.now()
                            duration = (end_time - start_time).total_seconds()

                            return await ctx.send(
                                content=ctx.s("canvas.dither").format(duration, type, option_string),
                                file=f)

            except aiohttp.client_exceptions.InvalidURL:
                raise UrlError
            except IOError:
                raise PilImageError

    async def _preview(self, ctx, args, name=None, fetch=None):
        """Sends a preview of the image or template provided.

        Arguments:
        ctx - A commands.Context object.
        args - A list of arguments from the user, all strings.

        Keyword Arguments:
        name - The name of the template to preview.
        fetch - A function to fetch from a specific canvas.
        """
        if not name:
            # Order Parsing
            try:
                x, y = args[0], args[1]
            except IndexError:
                await ctx.send("Error: no arguments were provided.")
                return

            if re.match(r"-\D+", x) is not None:
                x, y = args[-2], args[-1]
                args = args[:-2]
            else:
                args = args[2:]

            # X and Y Cleanup
            try:
                # Remove all spaces and chars that aren't 0-9 or the minus sign.
                x = int(re.sub('[^0-9-]', '', x))
                y = int(re.sub('[^0-9-]', '', y))
            except ValueError:
                await ctx.send(ctx.s("canvas.invalid_input"))
                return

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-z", "--zoom", type=int, default=1)

        if name:
            parser.add_argument("-t", "--templateRegion", action='store_true')
            parser.add_argument("-f", "--faction", default=None, action=FactionAction)

        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        t = None
        if name:
            gid = ctx.guild.id if not args.faction else args.faction.id
            t = sql.template_get_by_name(gid, name)

            if not t:
                raise TemplateNotFoundError(gid, name)

            fetch = self.bot.fetchers[t.canvas]
            if args.templateRegion:
                x, y = t.center()

        async with ctx.typing():
            zoom = max(min(args.zoom, 16), -8)

            if t:
                preview_img = await render.preview_template(t, zoom, fetch)
            else:
                preview_img = await render.preview(x, y, zoom, fetch)

            with io.BytesIO() as bio:
                preview_img.save(bio, format="PNG")
                bio.seek(0)
                f = discord.File(bio, "preview.png")
                await ctx.send(file=f)
    
    async def _quantize(self, ctx, args, canvas, palette):
        """Sends a message containing a quantised version of the image given.

        Arguments:
        ctx - A commands.Context object.
        args - A list of arguments from the user, all strings.
        canvas - The canvas to use, string.
        palette - The palette to quantise to, a list of rgb tuples.

        Returns:
        The discord.Message object returned when ctx.send() is called to send the quantised image.
        """
        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)

        # Pre-Parsing
        if len(args) == 0:
            name = None
        elif args[0][0] != "-":
            name = args[0]
            args = args[1:]
        else:
            name = None

        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        gid = ctx.guild.id if not args.faction else args.faction.id
        t = sql.template_get_by_name(gid, name)

        data = None
        if name:
            if t:
                log.info("(T:{} | GID:{})".format(t.name, t.gid))
                if t.canvas == canvas:
                    raise IdempotentActionError
                data = await http.get_template(t.url, t.name)
            else:
                raise TemplateNotFoundError(gid, name)
        else:
            att = await verify_attachment(ctx)
            if att:
                data = io.BytesIO()
                await att.save(data)

        if data:
            template, bad_pixels = await self.bot.loop.run_in_executor(None, render.quantize, data, palette)

            with io.BytesIO() as bio:
                template.save(bio, format="PNG")
                bio.seek(0)
                f = discord.File(bio, "template.png")
                return await ctx.send(ctx.s("canvas.quantize").format(bad_pixels), file=f)


class CheckSource(menus.ListPageSource):
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
                value="[{e}: {e_val}/{t_val} | {p}: {p_val}](https://pixelcanvas.io/@{x},{y})".format(
                    e=menu.ctx.s("bot.errors"),
                    e_val=template.errors,
                    t_val=template.size,
                    p=menu.ctx.s("bot.percent"),
                    p_val="{:>6.2f}%".format(100 * (template.size - template.errors) / template.size),
                    x=template.x,
                    y=template.y),
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
        raise BadArgument

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
    example_chunk = next(iter(chunks))
    for t in templates:
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


def setup(bot):
    bot.load_cog(Canvas(bot))