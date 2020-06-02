import asyncio
import datetime
from functools import partial
import hashlib
import io
import itertools
import logging
import math
import re
import time

import aiohttp
import discord
from discord.ext import commands, menus
from discord.ext.commands import BucketType
from PIL import Image

from objects import DbTemplate
from objects.errors import NoTemplatesError, PilImageError, TemplateNotFoundError, UrlError, TemplateHttpError, NoJpegsError, NotPngError
import utils
from utils import canvases, checks, colors, config, http, render, GlimmerArgumentParser, FactionAction, sqlite as sql

log = logging.getLogger(__name__)


class TemplateSource(menus.ListPageSource):
    def __init__(self, data):
        super().__init__(data, per_page=10)

    async def format_page(self, menu, entries):
        embed = discord.Embed(
            title=menu.ctx.s("template.list_header"),
            description=f"Page {menu.current_page + 1} of {self.get_max_pages()}")
        embed.set_footer(
            text="Scroll using the reactions below to see other pages.")

        offset = menu.current_page * self.per_page
        for i, template in enumerate(entries, start=offset):
            if i == offset + self.per_page:
                break
            embed.add_field(
                name=template.name,
                value="[{0}, {1}](https://pixelcanvas.io/@{0},{1}) | [Link to file]({2})"
                        .format(template.x, template.y, template.url),
                inline=False)
        return embed


class Template(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @commands.group(name='template', invoke_without_command=True, aliases=['t'], case_insensitive=True)
    async def template(self, ctx, *args):
        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-p", "--page", type=int, default=1)
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        gid = ctx.guild.id
        if args.faction is not None:
            gid = args.faction.id

        templates = sql.template_get_all_by_guild_id(gid)
        if len(templates) < 1:
            raise NoTemplatesError()

        template_menu = menus.MenuPages(
            source=TemplateSource(templates),
            clear_reactions_after=True,
            timeout=300.0)
        template_menu.current_page = max(min(args.page - 1, template_menu.source.get_max_pages()), 0)
        await template_menu.start(ctx)

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
    @template.group(name='update', aliases=['u'], invoke_without_command=True, case_insensitive=True)
    async def template_update(self, ctx):
        await ctx.invoke_default("template.update")

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_update.command(name="pixelcanvas", aliases=['pc'])
    async def template_update_pixelcanvas(self, ctx, *args):
        log.info(f"g!t update run in {ctx.guild.name} with args: {args}")

        try:
            name = args[0]
        except TypeError:
            await ctx.send("Template not updated as no arguments were provided.")
            return

        if re.match(r"-\D+", name) is not None:
            name = args[-1]
            args = args[:-1]
        else:
            args = args[1:]

        orig_template = sql.template_get_by_name(ctx.guild.id, name)
        if not orig_template:
            raise TemplateNotFoundError(ctx.guild.id, name)

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-n", "--newName", nargs="?", default=None)
        parser.add_argument("-x", nargs="?", default=None)
        parser.add_argument("-y", nargs="?", default=None)
        # if -i not present, False
        # if no value after -i, True
        # if value after -i, capture
        parser.add_argument("-i", "--image", nargs="?", const=True, default=None)
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        out = []

        # Image is done first since I'm using the build_template method to update stuff,
        # and I don't want anything to have changed in orig_template before I use it
        if args.image:
            # Update image
            url = None
            if not isinstance(args.image, bool):
                url = args.image
            url = await Template.select_url_update(ctx, url, out)
            if url is None:
                return  # Sending the end is handled in select_url_update if it fails

            try:
                t = await Template.build_template(ctx, orig_template.name, orig_template.x, orig_template.y, url, "pixelcanvas")
            except TemplateHttpError:
                out.append("Updating file failed: Could not access URL for template.")
                return await Template.send_end(ctx, out)
            except NoJpegsError:
                out.append("Updating file failed: Seriously? A JPEG? Gross! Please create a PNG template instead.")
                return await Template.send_end(ctx, out)
            except NotPngError:
                out.append("Updating file failed: That command requires a PNG image.")
                return await Template.send_end(ctx, out)
            except (PilImageError, UrlError):
                out.append("Updating file failed.")
                return await Template.send_end(ctx, out)

            if t is None:
                out.append("Updating file failed.")
                return await Template.send_end(ctx, out)

            # TODO: Could check for md5 duplicates here
            sql.template_kwarg_update(
                ctx.guild.id,
                orig_template.name,
                url=t.url,
                md5=t.md5,
                w=t.width,
                h=t.height,
                size=t.size,
                date_modified=int(time.time()))
            out.append("File updated.")

        if args.x:
            try:
                x = int(re.sub('[^0-9-]', '', args.x))
            except ValueError:
                out.append("Updating x failed, value provided was not a number.")
                return await Template.send_end(ctx, out)

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, x=x, date_modified=int(time.time()))
            out.append(f"X coordinate changed from {orig_template.x} to {x}.")

        if args.y:
            try:
                y = int(re.sub('[^0-9-]', '', args.y))
            except ValueError:
                out.append("Updating y failed, value provided was not a number.")
                return await Template.send_end(ctx, out)

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, y=y, date_modified=int(time.time()))
            out.append(f"Y coordinate changed from {orig_template.y} to {y}.")

        if args.newName:
            dup_check = sql.template_get_by_name(ctx.guild.id, args.newName)
            if dup_check is not None:
                out.append(f"Updating name failed, the name {args.newName} is already in use.")
                return await Template.send_end(ctx, out)
            if len(args.newName) > config.MAX_TEMPLATE_NAME_LENGTH:
                out.append("Updating name failed: {}".format(ctx.s("template.err.name_too_long").format(config.MAX_TEMPLATE_NAME_LENGTH)))
                return await Template.send_end(ctx, out)
            if args.newName[0] == "-":
                out.append("Updating name failed: Names cannot begin with hyphens.")
                return await Template.send_end(ctx, out)
            try:
                _ = int(args.newName)
                out.append("Updating name failed: Names cannot be numbers.")
                return await Template.send_end(ctx, out)
            except ValueError:
                pass

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, new_name=args.newName, date_modified=int(time.time()))
            out.append(f"Nickname changed from {name} to {args.newName}.")

        await Template.send_end(ctx, out)

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @template.command(name='info', aliases=['i'])
    async def template_info(self, ctx, *args):
        # Order Parsing
        try:
            name = args[0]
        except IndexError:
            return await ctx.send("Error: not enough arguments were provided.")

        if re.match(r"-\D+", name) is not None:
            name = args[-1]
            args = args[:-1]
        else:
            args = args[1:]

        # Argument Parsing
        parser = GlimmerArgumentParser(ctx)
        parser.add_argument("-r", "--raw", action="store_true")
        parser.add_argument("-f", "--faction", default=None, action=FactionAction)
        parser.add_argument("-z", "--zoom", default=1)
        try:
            args = parser.parse_args(args)
        except TypeError:
            return

        try:
            gid, faction = args.faction.id, args.faction
        except AttributeError:
            gid, faction = ctx.guild.id, sql.guild_get_by_id(ctx.guild.id)

        t = sql.template_get_by_name(gid, name)
        if not t:
            raise TemplateNotFoundError(gid, name)

        if args.raw:
            try:
                zoom = int(args.zoom)
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
            raise TemplateNotFoundError(ctx.guild.id, name)
        log.info("(T:{} G:{})".format(t.name, t.gid))
        if t.owner_id != ctx.author.id and not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.not_owner"))
            return
        sql.template_delete(t.gid, t.name)
        await ctx.send(ctx.s("template.remove").format(name))

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='snapshot', aliases=['s'], invoke_without_command=True, case_insensitive=True)
    async def template_snapshot(self, ctx, *filter):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            return await ctx.send(ctx.s("template.err.not_owner"))

        snapshots = sql.snapshots_get_all_by_guild(ctx.guild.id)
        if snapshots == []:
            return await ctx.send(f"No snapshots found, add some using `{ctx.gprefix}template snapshot add`")

        if filter != ():
            for i, snapshot in enumerate(snapshots):
                if snapshot[0].name not in filter:
                    snapshots[i] = None

        snapshots = [s for s in snapshots if s is not None]

        not_updated = []

        for i, (base, target) in enumerate(snapshots):
            await ctx.send(f"Checking {target.name} for errors...")
            data = await http.get_template(target.url, target.name)
            fetch = self.bot.fetchers[target.canvas]
            img = await fetch(target.x, target.y, target.width, target.height)
            func = partial(render.diff, target.x, target.y, data, 1, img, colors.by_name[target.canvas])
            diff_img, tot, err, bad, _err, _bad = await self.bot.loop.run_in_executor(None, func)
            if err == 0:
                query = await utils.yes_no(ctx, "There are no errors on the snapshot, do you want to update it?", cancel=True)
                if query is False:
                    not_updated.append([base, "skip"])
                    continue
                elif query == "cancel":
                    for (b, t) in snapshots[i:]:
                        not_updated.append([b, "cancel"])
                    break

            else:
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
                    msg = await ctx.send(content=out, file=f)
                query = await utils.yes_no(ctx, "There are errors on the snapshot, do you want to update it? You will loose track of progress if you do this.", cancel=True)
                if query is False:
                    not_updated.append([base, "err"])
                    continue
                elif query == "cancel":
                    for (b, t) in snapshots[i:]:
                        not_updated.append([b, "cancel"])
                    break

            await ctx.send(f"Generating snapshot from {base.name}...")
            data = await http.get_template(base.url, base.name)
            fetch = self.bot.fetchers[base.canvas]
            img = await fetch(base.x, base.y, base.width, base.height)
            func = partial(
                render.diff, base.x, base.y, data, 1,
                img, colors.by_name[base.canvas], create_snapshot=True)
            diff_img, tot, err, bad, _err, _bad = await self.bot.loop.run_in_executor(None, func)

            if bad == 0:
                with io.BytesIO() as bio:
                    diff_img.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, "diff.png")
                    msg = await ctx.send(file=f)

                url = msg.attachments[0].url
                await Template.add_template(ctx, base.canvas, target.name, str(base.x), str(base.y), url)
            else:
                not_updated.append([base, "bad"])
                continue

        if not_updated != []:
            out = []
            for t, reason in not_updated:
                reasons = {
                    "err": f"The snapshot of {t.name} was not updated, as there were errors on the current snapshot.",
                    "bad": f"The snapshot of {t.name} was not updated, as there were unquantised pixels detected.",
                    "cancel": f"The snapshot of {t.name} was not updated, as the command was cancelled.",
                    "skip": f"The snapshot of {t.name} was not updated, as the template was skipped."
                }
                text = reasons.get(reason)
                if text:
                    out.append(text)

            await ctx.send("```{}```".format("\n".join(out)))

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_snapshot.command(name='add', aliases=['a'])
    async def template_snapshot_add(self, ctx, base_template, snapshot_template):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.not_owner"))
            return

        base = sql.template_get_by_name(ctx.guild.id, base_template)
        target = sql.template_get_by_name(ctx.guild.id, snapshot_template)

        if base is None:
            return await ctx.send("The base template does not exist.")
        if target is None:
            return await ctx.send("The snapshot template does not exist.")

        sql.snapshot_add(ctx.guild.id, base_template, snapshot_template)
        await ctx.send("Snapshot added!")

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_snapshot.command(name='remove', aliases=['r'])
    async def template_snapshot_remove(self, ctx, base_template, snapshot_template):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            return await ctx.send(ctx.s("template.err.not_owner"))

        s = sql.snapshot_get_by_names(ctx.guild.id, base_template, snapshot_template)
        if s is None:
            return await ctx.send("That snapshot does not exist.")

        sql.snapshot_delete(ctx.guild.id, base_template, snapshot_template)
        await ctx.send("Snapshot removed!")

    @commands.guild_only()
    @commands.cooldown(2, 5, BucketType.guild)
    @checks.template_adder_only()
    @template_snapshot.command(name='list', aliases=['l'])
    async def template_snapshot_list(self, ctx):
        snapshots = sql.snapshots_get_all_by_guild(ctx.guild.id)
        if snapshots == []:
            return await ctx.send(f"No snapshots found, add some using `{ctx.gprefix}template snapshot add`")

        out = [f"Base Template Name:{base.name} Snapshot Template Name:{target.name}" for base, target in snapshots]
        await ctx.send("Snapshots:```{}```".format("\n".join(out)))

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
            return await ctx.send(ctx.s("template.err.name_too_long").format(config.MAX_TEMPLATE_NAME_LENGTH))
        if name[0] == "-":
            return await ctx.send("Template names cannot begin with hyphens.")
        try:
            _ = int(name)
            return await ctx.send("Template names cannot be numbers.")
        except ValueError:
            pass
        if sql.template_count_by_guild_id(ctx.guild.id) >= config.MAX_TEMPLATES_PER_GUILD:
            return await ctx.send(ctx.s("template.err.max_templates"))
        url = await Template.select_url(ctx, url)
        if url is None:
            return await ctx.send(ctx.s("template.err.no_image"))
        try:
            # Removes all spaces and chars that aren't 0-9 or the minus sign.
            x = int(re.sub('[^0-9-]', '', x))
            y = int(re.sub('[^0-9-]', '', y))
        except ValueError:
            return await ctx.send(ctx.s("template.err.invalid_coords"))

        t = await Template.build_template(ctx, name, x, y, url, canvas)
        if not t:
            return await ctx.send(ctx.s("template.err.template_gen_error"))
        log.info("(T:{} | X:{} | Y:{} | Dim:{})".format(t.name, t.x, t.y, t.size))
        name_chk = await Template.check_for_duplicate_by_name(ctx, t)
        md5_chk = await Template.check_for_duplicates_by_md5(ctx, t)

        if md5_chk is not None:
            dups = md5_chk
            dup_msg = ["```xl"]
            w = max(map(lambda tx: len(tx.name), dups)) + 2
            for d in dups:
                name = '"{}"'.format(d.name)
                canvas_name = canvases.pretty_print[d.canvas]
                dup_msg.append("{0:<{w}} {1:>15} {2}, {3}\n".format(name, canvas_name, d.x, d.y, w=w))
            dup_msg.append("```")

        if name_chk is not None:
            d = name_chk
            msg = [ctx.s("template.name_exists_ask_replace").format(
                d.name, canvases.pretty_print[d.canvas], d.x, d.y)]

            if name_chk is False:
                return
            elif md5_chk is not None:
                msg.append(ctx.s("template.duplicate_list_open"))
                msg = msg + dup_msg
                msg.append(ctx.s("template.replace"))
            else:
                msg = ["{} {}".format(msg[0], ctx.s("template.replace"))]

            if await utils.yes_no(ctx, "\n".join(msg)) is False:
                return await ctx.send(ctx.s("template.menuclose"))

            sql.template_update(t)
            return await ctx.send(ctx.s("template.updated").format(name))

        if md5_chk is not None:
            dup_msg.insert(0, ctx.s("template.duplicate_list_open"))
            dup_msg.append(ctx.s("template.duplicate_list_close"))
            if await utils.yes_no(ctx, "\n".join(dup_msg)) is False:
                return await ctx.send(ctx.s("template.menuclose"))

        sql.template_add(t)
        await ctx.send(ctx.s("template.added").format(name))

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

                    template, bad_pixels = render.quantize(data, colors.by_name[canvas])
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
        """Checks for duplicates using md5 hashing, returns the list of duplicates if any exist.

        Arguments:
        ctx - commands.Context object.
        template - A template object.

        Returns:
        A list or nothing.
        """
        dups = sql.template_get_by_hash(ctx.guild.id, template.md5)
        return dups if len(dups) > 0 else None

    @staticmethod
    async def check_for_duplicate_by_name(ctx, template):
        """Checks for duplicates by name, returns a that template if one exists and the user has
        permission to overwrite, False if they do not. None is returned if no other templates share
        this name.

        Arguments:
        ctx - commands.Context.
        template - A template object.

        Returns:
        A template object, False or None.
        """
        dup = sql.template_get_by_name(ctx.guild.id, template.name)
        if dup:
            if template.owner_id != ctx.author.id and not utils.is_admin(ctx):
                await ctx.send(ctx.s("template.err.name_exists"))
                return False
            return dup

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
            if re.search(r"^(?:https?://)cdn\.discordapp\.com/", input_url):
                return input_url

            out.append("Updating image failed, invalid url, it must be a discord attachment.")
            await Template.send_end(ctx, out)
            return

        # there was no url in the text of the message, is there an attachment?
        if len(ctx.message.attachments) > 0:
            return ctx.message.attachments[0].url

        out.append("Updating image failed, no attachments could be detected.")
        await Template.send_end(ctx, out)
        return

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
            if re.search(r"^(?:https?://)cdn\.discordapp\.com/", input_url):
                return input_url
            raise UrlError
        if len(ctx.message.attachments) > 0:
            return ctx.message.attachments[0].url
