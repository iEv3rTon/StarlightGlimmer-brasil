import asyncio
import datetime
import hashlib
import io
import itertools
import logging
import math
import re
import time
from typing import List
from prettytable import *

import aiohttp
import discord
import numpy as np
from discord.ext import commands
from discord.ext.commands import BucketType
from PIL import Image, ImageChops

from objects import DbTemplate
from objects.chunks import BigChunk, ChunkPz, PxlsBoard
from objects.errors import FactionNotFoundError, NoTemplatesError, PilImageError, TemplateNotFoundError, UrlError, IgnoreError
import utils
from utils import canvases, checks, colors, config, http, render, sqlite as sql

log = logging.getLogger(__name__)


class Template(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(name='template', invoke_without_command=True, aliases=['t'], case_insensitive=True)
    async def template(self, ctx, *args):
        gid = ctx.guild.id
        iter_args = iter(args)
        page = next(iter_args, 1)
        if page == "-f":
            fac = next(iter_args, None)
            if fac is None:
                await ctx.send(ctx.s("error.missing_arg_faction"))
                return
            faction = sql.guild_get_by_faction_name_or_alias(fac)
            if not faction:
                raise FactionNotFoundError
            gid = faction.id
            page = next(iter_args, 1)
        try:
            page = int(page)
        except ValueError:
            page = 1

        templates = sql.template_get_all_by_guild_id(gid)
        if len(templates) < 1:
            raise NoTemplatesError()

        # Find number of pages given there are 25 templates per page.
        pages = int(math.ceil(len(templates) / 25))
        # Makes sure page is in the range (1 <= page <= pages).
        page = min(max(page, 0), pages)
        page_index = page - 1

        embed = Template.build_table(ctx, page_index, pages, templates)
        message = await ctx.send(embed=embed)
        await message.add_reaction('◀')
        await message.add_reaction('▶')

        def is_valid(reaction, user):
            return reaction.message.id == message.id and (reaction.emoji == '◀' or reaction.emoji == '▶') and user.id != 589606792926068736

        _5_minutes_in_future = (datetime.datetime.today() + datetime.timedelta(minutes=5.0))

        try:
            while _5_minutes_in_future > datetime.datetime.today():
                reaction, _user = await self.bot.wait_for('reaction_add', timeout=300.0, check=is_valid)
                if reaction.emoji == '◀':
                    if page_index != 0:
                        #not on first page, scroll left
                        page_index -= 1
                        embed = Template.build_table(ctx, page_index, pages, templates)
                        await message.edit(embed=embed)
                elif reaction.emoji == '▶':
                    if page_index != pages-1:
                        #not on last page, scroll right
                        page_index += 1
                        embed = Template.build_table(ctx, page_index, pages, templates)
                        await message.edit(embed=embed)
        except asyncio.TimeoutError:
            pass
        await message.edit(content="**Menu timed out.**", embed=embed)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @template.command(name='all')
    async def template_all(self, ctx, page: int = 1):
        gs = [x for x in sql.guild_get_all_factions() if x.id not in sql.faction_hides_get_all(ctx.guild.id)]
        ts = [x for x in sql.template_get_all() if x.gid in [y.id for y in gs]]

        def by_faction_name(template):
            for g in gs:
                if template.gid == g.id:
                    return g.faction_name

        ts = sorted(ts, key=by_faction_name)
        ts_with_f = []
        for faction, ts2 in itertools.groupby(ts, key=by_faction_name):
            for t in ts2:
                ts_with_f.append((t, faction))

        if len(ts) > 0:
            pages = 1 + len(ts) // 10
            page = min(max(page, 1), pages)
            w1 = max(max(map(lambda tx: len(tx.name), ts)) + 2, len(ctx.s("bot.name")))
            msg = [
                "**{}** - {} {}/{}".format(ctx.s("template.list_header"), ctx.s("bot.page"), page, pages),
                "```xl",
                "{0:<{w1}}  {1:<34}  {2:<14}  {3}".format(ctx.s("bot.name"),
                                                          ctx.s("bot.faction"),
                                                          ctx.s("bot.canvas"),
                                                          ctx.s("bot.coordinates"), w1=w1)
            ]
            for t, f in ts_with_f[(page - 1) * 10:page * 10]:
                coords = "{}, {}".format(t.x, t.y)
                faction = '"{}"'.format(f)
                name = '"{}"'.format(t.name)
                canvas_name = canvases.pretty_print[t.canvas]
                msg.append("{0:<{w1}}  {1:<34}  {2:<14}  {3}".format(name, faction, canvas_name, coords, w1=w1))
            msg.append("")
            msg.append("// " + ctx.s("template.list_all_footer_1").format(ctx.gprefix))
            msg.append("// " + ctx.s("template.list_all_footer_2").format(ctx.gprefix))
            msg.append("```")
            await ctx.send('\n'.join(msg))
        else:
            await ctx.send(ctx.s("template.err.no_public_templates"))

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='add', invoke_without_command=True, case_insensitive=True)
    async def template_add(self, ctx):
        await ctx.invoke_default("template.add")

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pixelcanvas", aliases=['pc'])
    async def template_add_pixelcanvas(self, ctx, name: str, x, y, url=None):
        await self.add_template(ctx, "pixelcanvas", name, x, y, url)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pixelzone", aliases=['pz'])
    async def template_add_pixelzone(self, ctx, name: str, x, y, url=None):
        await self.add_template(ctx, "pixelzone", name, x, y, url)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pxlsspace", aliases=['ps'])
    async def template_add_pxlsspace(self, ctx, name: str, x, y, url=None):
        await self.add_template(ctx, "pxlsspace", name, x, y, url)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='update', invoke_without_command=True, case_insensitive=True)
    async def template_update(self, ctx):
        await ctx.invoke_default("template.update")

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_update.command(name="pixelcanvas", aliases=['pc'])
    async def template_update_pixelcanvas(self, ctx, name, *args):
        log.info(f"g!t update run in {ctx.guild.name} with name: {name} and args: {args}")

        orig_template = sql.template_get_by_name(ctx.guild.id, name)
        if not orig_template:
            raise TemplateNotFoundError

        # Argument Parsing
        parser = argparse.ArgumentParser(description="Parses update args")
        parser.add_argument("-n", "--newName", default=False)
        parser.add_argument("-x", default=False)
        parser.add_argument("-y", default=False)
        # if -i not present, False
        # if no value after -i, True
        # if value after -i, capture
        parser.add_argument("-i", "--image", nargs="?", const=True, default=False)
        args = parser.parse_known_args(args)
        unknown = args[1]
        args = vars(args[0])

        new_name = args["newName"]
        x = args["x"]
        y = args["y"]
        image = args["image"]

        out = []

        # Any unrecognised arguments are reported
        if unknown != []:
            for value in unknown:
                out.append(f"Unrecognised argument: {value}")

        """Image is done first since I'm using the build_template method to update stuff, 
        and I don't want anything to have changed in orig_template before I use it"""
        if image != False:
            # Update image
            url = None
            if not isinstance(image, bool):
                url = image
            url = await template.select_url_update(ctx, url, out)
            if url is None:
                return # Sending the end is handled in select_url_update if it fails

            t = await Template.build_template(ctx, orig_template.name, orig_template.x, orig_template.y, url, "pixelcanvas")
            if t is None:
                out.append(f"Updating file failed")
                await template.send_end(ctx, out)
                return

            # Could check for md5 duplicates here, maybe implement that later
            sql.template_kwarg_update(
                orig_template.id,
                url=t.url,
                md5=t.md5,
                w=t.w,
                h=t.h,
                size=t.size,
                date_modified=int(time.time()))
            out.append(f"File updated.")

        if new_name != False:
            # Check if new name is already in use
            dup_check = sql.template_get_by_name(ctx.guild.id, new_name)
            if dup_check != None:
                out.append(f"Updating name failed, the name {new_name} is already in use")
                await Template.send_end(ctx, out)
                return
            # Check if new name is too long
            if len(new_name) > config.MAX_TEMPLATE_NAME_LENGTH:
                out.append("Updating name failed: "+ctx.s("template.err.name_too_long").format(config.MAX_TEMPLATE_NAME_LENGTH))
                await Template.send_end(ctx, out)
                return

            # None with new nick, update template
            sql.template_kwarg_update(orig_template.id, name=new_name, date_modified=int(time.time()))
            out.append(f"Nickname changed from {name} to {new_name}")

        if x != False:
            # Update x coord
            try:
                x = int(re.sub('[^0-9-]','', x))
            except ValueError:
                out.append("Updating x failed, value provided was not a number.")
                await Template.send_end(ctx, out)
                return

            sql.template_kwarg_update(orig_template.id, x=x, date_modified=int(time.time()))
            out.append(f"X coordinate changed from {t.x} to {x}.")

        await Template.send_end(ctx, out)

        if y != False:
            # Update y coord
            try:
                y = int(re.sub('[^0-9-]','', y))
            except ValueError:
                out.append("Updating y failed, value provided was not a number.")
                await template.send_end(ctx, out)
                return

            sql.template_kwarg_update(orig_template.id, y=y, date_modified=int(time.time()))
            out.append(f"Y coordinate changed from {t.y} to {y}.")   

    @commands.guild_only()
    @commands.cooldown(1, 10, BucketType.guild)
    @template.group(name='check', invoke_without_command=True, case_insensitive=True)
    async def template_check(self, ctx, page=1):
        templates = sql.template_get_all_by_guild_id(ctx.guild.id)

        if len(templates) < 1:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(False)

        msg = None
        templates = sorted(templates, key=lambda tx: tx.name)
        templates = sorted(templates, key=lambda tx: tx.canvas)

        # Find number of pages given there are 10 templates per page.
        pages = int(math.ceil(len(templates) / 10))
        # Make sure page is in the range (1 <= page <= pages).
        page = min(max(page, 0), pages)

        # Slice so templates only contains the page we want
        start = (page-1)*10
        end = page*10
        templates = templates[start:end]

        # Calc info + send temp msg
        for canvas, canvas_ts in itertools.groupby(templates, lambda tx: tx.canvas):
            ct = list(canvas_ts)
            msg = await Template.check_canvas(ctx, ct, canvas, msg=msg)

        # Delete temp msg and send final report
        await msg.delete()
        await Template.build_template_report(ctx, templates, page, pages)

    @commands.guild_only()
    @commands.cooldown(1, 40, BucketType.default)
    @template_check.command(name='all', aliases=['a'])
    async def template_check_all(self, ctx, only_errors=None):
        if only_errors == "-e" or only_errors == "--error":
            only_errors = True

        templates = sql.template_get_all_by_guild_id(ctx.guild.id)

        if len(templates) < 1:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(False)

        msg = None
        templates = sorted(templates, key=lambda tx: tx.name)
        templates = sorted(templates, key=lambda tx: tx.canvas)

        # Calc info + send temp msg
        for canvas, canvas_ts in itertools.groupby(templates, lambda tx: tx.canvas):
            ct = list(canvas_ts)
            msg = await Template.check_canvas(ctx, ct, canvas, msg=msg)

        # Delete temp msg and send final report
        await msg.delete()

        if only_errors:
            ts = []
            for template in templates:
                if template.errors != 0:
                    ts.append(template)

            # Find number of pages given there are 25 templates per page.
            pages = int(math.ceil(len(ts) / 25))
            await Template.build_template_report(ctx, ts, None, pages)
        else:
            # Find number of pages given there are 25 templates per page.
            pages = int(math.ceil(len(templates) / 25))
            await Template.build_template_report(ctx, templates, None, pages)

    @commands.guild_only()
    @template_check.command(name='pixelcanvas', aliases=['pc'])
    async def template_check_pixelcanvas(self, ctx, page=1):
        templates = [x for x in sql.template_get_all_by_guild_id(ctx.guild.id) if x.canvas == 'pixelcanvas']
        if len(templates) <= 0:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(True)
        templates = sorted(templates, key=lambda tx: tx.name)

        # Find number of pages given there are 10 templates per page.
        pages = int(math.ceil(len(templates) / 10))
        # Make sure page is in the range (1 <= page <= pages).
        page = min(max(page, 0), pages)

        # Slice so templates only contains the page we want
        start = (page-1)*10
        end = page*10
        templates = templates[start:end]

        # Calc info + send temp msg
        msg = await Template.check_canvas(ctx, templates, "pixelcanvas")
        # Delete temp msg and send final report
        await msg.delete()
        await Template.build_template_report(ctx, templates, page, pages)

    @commands.guild_only()
    @template_check.command(name='pixelzone', aliases=['pz'])
    async def template_check_pixelzone(self, ctx, page=1):
        templates = [x for x in sql.template_get_all_by_guild_id(ctx.guild.id) if x.canvas == 'pixelzone']
        if len(templates) <= 0:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(True)
        templates = sorted(templates, key=lambda tx: tx.name)

        # Find number of pages given there are 10 templates per page.
        pages = int(math.ceil(len(templates) / 10))
        # Make sure page is in the range (1 <= page <= pages).
        page = min(max(page, 0), pages)

        # Slice so templates only contains the page we want
        start = (page-1)*10
        end = page*10
        templates = templates[start:end]

        # Calc info + send temp msg
        msg = await Template.check_canvas(ctx, templates, "pixelzone")
        # Delete temp msg and send final report
        await msg.delete()
        await Template.build_template_report(ctx, templates, page, pages)

    @commands.guild_only()
    @template_check.command(name='pxlsspace', aliases=['ps'])
    async def template_check_pxlsspace(self, ctx, page=1):
        # Get all templates that could be checked, sort them
        templates = [x for x in sql.template_get_all_by_guild_id(ctx.guild.id) if x.canvas == 'pxlsspace']
        if len(templates) <= 0:
            ctx.command.parent.reset_cooldown(ctx)
            raise NoTemplatesError(True)
        templates = sorted(templates, key=lambda tx: tx.name)

        # Find number of pages given there are 10 templates per page.
        pages = int(math.ceil(len(templates) / 10))
        # Make sure page is in the range (1 <= page <= pages).
        page = min(max(page, 0), pages)

        # Slice so templates only contains the page we want
        start = (page-1)*10
        end = page*10
        templates = templates[start:end]

        # Calc info + send temp msg
        msg = await Template.check_canvas(ctx, templates, "pxlsspace")
        # Delete temp msg and send final report
        await msg.delete()
        await Template.build_template_report(ctx, templates, page, pages)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @template.command(name='info', aliases=['i'])
    async def template_info(self, ctx, *args):
        gid = ctx.guild.id
        iter_args = iter(args)
        name = next(iter_args, 1)
        image_only = False
        if name == "-r":
            image_only = True
            name = next(iter_args, 1)
        if name == "-f":
            fac = next(iter_args, None)
            if fac is None:
                await ctx.send(ctx.s("error.missing_arg_faction"))
                return
            faction = sql.guild_get_by_faction_name_or_alias(fac)
            if not faction:
                raise FactionNotFoundError
            gid = faction.id
            name = next(iter_args, 1)
        else:
            faction = sql.guild_get_by_id(gid)
        t = sql.template_get_by_name(gid, name)
        if not t:
            raise TemplateNotFoundError

        if image_only:
            zoom = next(iter_args, 1)
            try:
                if type(zoom) is not int:
                    if zoom.startswith("#"):
                        zoom = zoom[1:]
                    zoom = int(zoom)
            except ValueError:
                zoom = 1
            max_zoom = int(math.sqrt(4000000 // (t.width * t.height)))
            zoom = max(1, min(zoom, max_zoom))

            img = render.zoom(await http.get_template(t.url, t.name), zoom)

            with io.BytesIO() as bio:
                img.save(bio, format="PNG")
                bio.seek(0)
                f = discord.File(bio, t.name + ".png")
                await ctx.send(file=f)
            return

        canvas_name = canvases.pretty_print[t.canvas]
        coords = "{}, {}".format(t.x, t.y)
        dimensions = "{} x {}".format(t.width, t.height)
        size = t.size
        visibility = ctx.s("bot.private") if bool(t.private) else ctx.s("bot.public")
        owner = self.bot.get_user(t.owner_id)
        if owner is None:
            added_by = ctx.s("error.account_deleted")
        else:
            added_by = owner.name + "#" + owner.discriminator
        date_added = datetime.date.fromtimestamp(t.date_created).strftime("%d %b, %Y")
        date_modified = datetime.date.fromtimestamp(t.date_updated).strftime("%d %b, %Y")
        color = faction.faction_color
        description = "[__{}__]({})".format(ctx.s("template.link_to_canvas"),
                                            canvases.url_templates[t.canvas].format(*t.center()))

        if size == 0:
            t.size = await render.calculate_size(await http.get_template(t.url, t.name))
            sql.template_update(t)

        e = discord.Embed(title=t.name, color=color, description=description) \
            .set_image(url=t.url) \
            .add_field(name=ctx.s("bot.canvas"), value=canvas_name, inline=True) \
            .add_field(name=ctx.s("bot.coordinates"), value=coords, inline=True) \
            .add_field(name=ctx.s("bot.dimensions"), value=dimensions, inline=True) \
            .add_field(name=ctx.s("bot.size"), value=size, inline=True) \
            .add_field(name=ctx.s("bot.visibility"), value=visibility, inline=True) \
            .add_field(name=ctx.s("bot.added_by"), value=added_by, inline=True) \
            .add_field(name=ctx.s("bot.date_added"), value=date_added, inline=True) \
            .add_field(name=ctx.s("bot.date_modified"), value=date_modified, inline=True)

        if faction.id != ctx.guild.id and faction.faction_name:
            e = e.set_author(name=faction.faction_name, icon_url=faction.faction_emblem or discord.Embed.Empty)

        await ctx.send(embed=e)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template.command(name='remove', aliases=['rm'])
    async def template_remove(self, ctx, name):
        t = sql.template_get_by_name(ctx.guild.id, name)
        if not t:
            raise TemplateNotFoundError
        log.info("(T:{})".format(t.name, t.gid))
        if t.owner_id != ctx.author.id and not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.not_owner"))
            return
        sql.template_delete(t.gid, t.name)
        await ctx.send(ctx.s("template.remove").format(name))

    @staticmethod
    async def add_template(ctx, canvas, name, x, y, url):
        """Adds a template to the database.

        Arguments:
        ctx - commands.Context object.
        canvas - The canvas that the template is for, string.
        name - The name of the template, string.
        x - The x coordinate of the template, integer.
        y - The y coordinate of the template, integer.
        url - The url of the template's image, string.
        """
        if len(name) > config.MAX_TEMPLATE_NAME_LENGTH:
            await ctx.send(ctx.s("template.err.name_too_long").format(config.MAX_TEMPLATE_NAME_LENGTH))
            return
        if sql.template_count_by_guild_id(ctx.guild.id) >= config.MAX_TEMPLATES_PER_GUILD:
            await ctx.send(ctx.s("template.err.max_templates"))
            return
        url = await Template.select_url(ctx, url)
        if url is None:
            await ctx.send(ctx.s("template.err.no_image"))
            return
        try:
            #cleans up x and y by removing all spaces and chars that aren't 0-9 or the minus sign using regex. Then makes em ints
            x = int(re.sub('[^0-9-]','', x))
            y = int(re.sub('[^0-9-]','', y))
        except ValueError:
            await ctx.send(ctx.s("template.err.invalid_coords"))
            return

        t = await Template.build_template(ctx, name, x, y, url, canvas)
        if not t:
            await ctx.send(ctx.s("template.err.template_gen_error"))
            return
        log.info("(T:{} | X:{} | Y:{} | Dim:{})".format(t.name, t.x, t.y, t.size))
        chk = await Template.check_for_duplicate_by_name(ctx, t)
        if chk is not None:
            if not chk or await Template.check_for_duplicates_by_md5(ctx, t) is False:
                return
            sql.template_update(t)
            await ctx.send(ctx.s("template.updated").format(name))
            return
        elif await Template.check_for_duplicates_by_md5(ctx, t) is False:
            return
        sql.template_add(t)
        await ctx.send(ctx.s("template.added").format(name))

    @staticmethod
    def build_table(ctx, page_index, pages, t):
        """Builds a template embed page.

        Arguments:
        ctx - commands.Context object.
        page_index - The index of the page you wish to fetch an embed for, counts from 0, integer.
        pages - The total number of pages there are for the set of templates you are building an embed for, integer.
        t - A list of template objects.

        Returns:
        A fully formatted discord.Embed object.
        """
        embed = discord.Embed(
            title=ctx.s("template.list_header"),
            description=f"Page {page_index+1} of {pages}")
        embed.set_footer(text=f"Do {ctx.gprefix}t check <page_number> to see other pages, or scroll using the reactions below.")

        # Go through pages until the page requested is equal to the current page.
        for p in range(pages):
            if p == page_index:
                # Try to pop 25 template objects from t into the embed.
                for template in range(25):
                    # Use calculation (page*25) to find the position to iterate from in list.
                    try:
                        row = t[(p*25)+template]
                        embed.add_field(
                            name=row.name,
                            value="[{0}, {1}](https://pixelcanvas.io/@{0},{1}) | [Link to file]({2})".format(row.x, row.y, row.url),
                            inline=False)
                    except:
                        pass
        return embed

    @staticmethod
    async def build_template(ctx, name, x, y, url, canvas):
        """ Builds a template object from the given data.

        Arguments:
        ctx - commands.Context object.
        name - The name of the template, string.
        x - The x coordinate of the template, integer.
        y - The y coordinate of the template, integer.
        url - The url of the template’s image, string.
        canvas - The canvas this template is on, string.

        Returns:
        A template object.
        """
        try:
            with await http.get_template(url, name) as data:
                size = await render.calculate_size(data)
                md5 = hashlib.md5(data.getvalue()).hexdigest()
                with Image.open(data).convert("RGBA") as tmp:
                    w, h = tmp.size
                    quantized = await Template.check_colors(tmp, colors.by_name[canvas])
                if not quantized:
                    if not await utils.yes_no(ctx, ctx.s("template.not_quantized")):
                        ctx.send(ctx.s("template.menuclose"))
                        return

                    template, bad_pixels = await render.quantize(data, colors.by_name[canvas])
                    with io.BytesIO() as bio:
                        template.save(bio, format="PNG")
                        bio.seek(0)
                        f = discord.File(bio, "template.png")
                        new_msg = await ctx.send(ctx.s("canvas.quantize").format(bad_pixels), file=f)

                    url = new_msg.attachments[0].url
                    with await http.get_template(url, name) as data2:
                        md5 = hashlib.md5(data2.getvalue()).hexdigest()
                created = int(time.time())
                return DbTemplate(ctx.guild.id, name, url, canvas, x, y, w, h, size, created, created, md5,
                                  ctx.author.id)
        except aiohttp.client_exceptions.InvalidURL:
            raise UrlError
        except IOError:
            raise PilImageError

    @staticmethod
    async def check_colors(img, palette):
        """Checks if an image is quantised.

        Arguments:
        img - A PIL Image object.
        palette - The palette to check against, a list of rgb tuples.

        Returns:
        A boolean.
        """
        for py in range(img.height):
            await asyncio.sleep(0)
            for px in range(img.width):
                pix = img.getpixel((px, py))
                if pix[3] == 0:
                    continue
                if pix[3] != 255:
                    return False
                if pix[:3] not in palette:
                    return False
        return True

    @staticmethod
    async def check_for_duplicates_by_md5(ctx, template):
        """Checks for duplicates using md5 hashing, will bypass this check if the user verifies that they want to.

        Arguments:
        ctx - commands.Context object.
        template - A template object.

        Returns:
        A boolean or nothing.
        """
        dups = sql.template_get_by_hash(ctx.guild.id, template.md5)
        if len(dups) > 0:
            msg = [ctx.s("template.duplicate_list_open"),
                   "```xl"]
            w = max(map(lambda tx: len(tx.name), dups)) + 2
            for d in dups:
                name = '"{}"'.format(d.name)
                canvas_name = canvases.pretty_print[d.canvas]
                msg.append("{0:<{w}} {1:>15} {2}, {3}\n".format(name, canvas_name, d.x, d.y, w=w))
            msg.append("```")
            msg.append(ctx.s("template.duplicate_list_close"))
            yesnomenu = await utils.yes_no(ctx, '\n'.join(msg))
            if yesnomenu == True:
                return True
            else:
                await ctx.send(ctx.s("template.menuclose"))
                return False

    @staticmethod
    async def check_for_duplicate_by_name(ctx, template):
        """Checks for duplicates by name, will bypass and signal to overwrite if told to.

        Arguments:
        ctx - commands.Context.
        template - A template object.

        Returns:
        A boolean or nothing.
        """
        dup = sql.template_get_by_name(ctx.guild.id, template.name)
        if dup:
            if template.owner_id != ctx.author.id and not utils.is_admin(ctx):
                await ctx.send(ctx.s("template.err.name_exists"))
                return False
            q = ctx.s("template.name_exists_ask_replace") \
                .format(dup.name, canvases.pretty_print[dup.canvas], dup.x, dup.y)
            yesnomenu = await utils.yes_no(ctx, q)
            if yesnomenu == True:
                return True
            else:
                await ctx.send(ctx.s("template.menuclose"))
                return False

    @staticmethod
    async def select_url_update(ctx, input_url, out):
        """Selects the url from the user input or the attachments.

        Arguments:
        ctx - A commands.Context object.
        input_url - The user's input, string.
        out - Update changelog, list.

        Returns:
        A discord url, string.
        """
        # some text was sent in the url section of the parameters, check if it's a valid discord url
        if input_url:
            if re.search('^(?:https?://)cdn\.discordapp\.com/', input_url):
                return input_url

            out.append("Updating image failed, invalid url, it must be a discord attachment.")
            await template.send_end(ctx, out)
            return None

        # there was no url in the text of the message, is there an attachment
        if len(ctx.message.attachments) > 0:
            return ctx.message.attachments[0].url

        out.append("Updating image failed, no attachments could be detected.")
        await template.send_end(ctx, out)
        return None

    # downloads the image from discord
    @staticmethod
    async def get_template_u(ctx, url, out):
        """Downloads and opens an image as a bytestream.

        Arguments:
        ctx - A commands.Context object.
        url - The url of an image, string.
        out - Update changelog, list.

        Returns:
        The bytestream of the image.
        """
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url) as resp:
                if resp.status != 200:
                    out.append("Updating image failed, {} Error loading file.".format(resp.status))
                    await template.send_end(ctx, out)
                    raise IgnoreError
                if resp.content_type == "image/jpg" or resp.content_type == "image/jpeg":
                    out.append("Updating image failed, the image must be a png, not a jpeg.")
                    await template.send_end(ctx, out)
                    raise IgnoreError
                if resp.content_type != "image/png":
                    out.append("Updating image failed, the image must be a png.")
                    await template.send_end(ctx, out)
                    raise IgnoreError
                return io.BytesIO(await resp.read())

    @staticmethod
    async def send_end(ctx, out):
        if out != []:
            await ctx.send("Template updated!```{}```".format("\n".join(out)))
        else:
            await ctx.send("Template not updated as no arguments were provided.")

    @staticmethod
    async def select_url(ctx, input_url):
        """Selects a url from the available information.

        Arguments:
        ctx - commands.Context object.
        input_url - A string containing a possible url, or None.

        Returns:
        Nothing or a discord url, string.
        """
        if input_url:
            if re.search('^(?:https?://)cdn\.discordapp\.com/', input_url):
                return input_url
            raise UrlError
        if len(ctx.message.attachments) > 0:
            return ctx.message.attachments[0].url

    @staticmethod
    async def build_template_report(ctx, templates: List[DbTemplate], page, pages):
        """Builds and sends a template check embed on the set of templates provided.

        Arguments:
        ctx - commands.Context object.
        templates - A list of template objects.
        page - An integer specifying the page that the user is on, or nothing.
        pages - The total number of pages for the current set of templates, integer.
        """
        if page != None: # Sending one page
            embed = discord.Embed(
                title=ctx.s("template.template_report_header"),
                description=f"Page {page} of {pages}")
            embed.set_footer(text=f"Do {ctx.gprefix}t check <page_number> to see other pages")

            for x, template in enumerate(templates):
                embed.add_field(
                    name=template.name,
                    value="[{e}: {e_val}/{t_val} | {p}: {p_val}](https://pixelcanvas.io/@{x},{y})".format(
                        e=ctx.s("bot.errors"),
                        e_val=template.errors,
                        t_val=template.size,
                        p=ctx.s("bot.percent"),
                        p_val="{:>6.2f}%".format(100 * (template.size - template.errors) / template.size),
                        x=template.x,
                        y=template.y),
                    inline=False)
            await ctx.send(embed=embed)
        else: # Sending *all* pages
            for page in range(pages):
                page += 1
                # Slice so templates only contains the page we want
                start = (page-1)*25
                end = page*25
                templates_copy = templates[start:end]

                embed = discord.Embed(
                    title=ctx.s("template.template_report_header"),
                    description=f"Page {page} of {pages}")

                for x, template in enumerate(templates_copy):
                    embed.add_field(
                        name=template.name,
                        value="[{e}: {e_val}/{t_val} | {p}: {p_val}](https://pixelcanvas.io/@{x},{y})".format(
                            e=ctx.s("bot.errors"),
                            e_val=template.errors,
                            t_val=template.size,
                            p=ctx.s("bot.percent"),
                            p_val="{:>6.2f}%".format(100 * (template.size - template.errors) / template.size),
                            x=template.x,
                            y=template.y),
                        inline=False)
                await ctx.send(embed=embed)

    @staticmethod
    async def check_canvas(ctx, templates, canvas, msg=None):
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
            empty_bcs, shape = chunk_classes[canvas].get_intersecting(t.x, t.y, t.width, t.height)
            chunks.update(empty_bcs)

        if msg is not None:
            await msg.edit(content=ctx.s("template.fetching_data").format(canvases.pretty_print[canvas]))
        else:
            msg = await ctx.send(ctx.s("template.fetching_data").format(canvases.pretty_print[canvas]))
        await http.fetch_chunks(chunks) # Fetch all chunks

        await msg.edit(content=ctx.s("template.calculating"))
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
            template = Image.open(await http.get_template(t.url, t.name)).convert('RGBA')
            alpha = Image.new('RGBA', template.size, (255, 255, 255, 0))
            template = Image.composite(template, alpha, template)
            tmp = Image.composite(tmp, alpha, template)
            tmp = ImageChops.difference(tmp.convert('RGB'), template.convert('RGB'))
            t.errors = np.array(tmp).any(axis=-1).sum()

        return msg
