import datetime
from functools import partial
import io
import itertools
import logging
import math
import re
import time

import discord
from discord.ext import commands, menus

from commands.template.template_methods import \
    (build_template,
     add_template,
     send_end,
     select_url_update,
     Snapshot,
     TemplateSource,
     SnapshotSource)
from objects.errors import \
    (NoTemplatesError,
     PilImageError,
     TemplateNotFoundError,
     UrlError,
     TemplateHttpError,
     NoJpegsError,
     NotPngError)
import utils
from utils import \
    (canvases,
     checks,
     colors,
     config,
     http,
     render,
     GlimmerArgumentParser,
     FactionAction,
     sqlite as sql)

log = logging.getLogger(__name__)


class Template(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =======================
    #        Template
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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
        try:
            await template_menu.start(ctx, wait=True)
            template_menu.source.embed.set_footer(text=ctx.s("bot.timeout"))
            await template_menu.message.edit(embed=template_menu.source.embed)
        except discord.NotFound:
            await ctx.send(ctx.s("bot.menu_deleted"))

    # =======================
    #      Template All
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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

    # =======================
    #      Template Add
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='add', invoke_without_command=True, case_insensitive=True)
    async def template_add(self, ctx):
        await ctx.invoke_default("template.add")

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pixelcanvas", aliases=['pc'])
    async def template_add_pixelcanvas(self, ctx, name: str, x, y, url=None):
        await add_template(ctx, "pixelcanvas", name, x, y, url)

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pixelzone", aliases=['pz'])
    async def template_add_pixelzone(self, ctx, name: str, x, y, url=None):
        await add_template(ctx, "pixelzone", name, x, y, url)

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template_add.command(name="pxlsspace", aliases=['ps'])
    async def template_add_pxlsspace(self, ctx, name: str, x, y, url=None):
        await add_template(ctx, "pxlsspace", name, x, y, url)

    # =======================
    #     Template Update
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='update', aliases=['u'], invoke_without_command=True, case_insensitive=True)
    async def template_update(self, ctx):
        await ctx.invoke_default("template.update")

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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
            url = await select_url_update(ctx, url, out)
            if url is None:
                return  # Sending the end is handled in select_url_update if it fails

            try:
                t = await build_template(ctx, orig_template.name, orig_template.x, orig_template.y, url, "pixelcanvas")
            except TemplateHttpError:
                out.append("Updating file failed: Could not access URL for template.")
                return await send_end(ctx, out)
            except NoJpegsError:
                out.append("Updating file failed: Seriously? A JPEG? Gross! Please create a PNG template instead.")
                return await send_end(ctx, out)
            except NotPngError:
                out.append("Updating file failed: That command requires a PNG image.")
                return await send_end(ctx, out)
            except (PilImageError, UrlError):
                out.append("Updating file failed.")
                return await send_end(ctx, out)

            if t is None:
                out.append("Updating file failed.")
                return await send_end(ctx, out)

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
                return await send_end(ctx, out)

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, x=x, date_modified=int(time.time()))
            out.append(f"X coordinate changed from {orig_template.x} to {x}.")

        if args.y:
            try:
                y = int(re.sub('[^0-9-]', '', args.y))
            except ValueError:
                out.append("Updating y failed, value provided was not a number.")
                return await send_end(ctx, out)

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, y=y, date_modified=int(time.time()))
            out.append(f"Y coordinate changed from {orig_template.y} to {y}.")

        if args.newName:
            dup_check = sql.template_get_by_name(ctx.guild.id, args.newName)
            if dup_check is not None:
                out.append(f"Updating name failed, the name {args.newName} is already in use.")
                return await send_end(ctx, out)
            if len(args.newName) > config.MAX_TEMPLATE_NAME_LENGTH:
                out.append("Updating name failed: {}".format(ctx.s("template.err.name_too_long").format(config.MAX_TEMPLATE_NAME_LENGTH)))
                return await send_end(ctx, out)
            if args.newName[0] == "-":
                out.append("Updating name failed: Names cannot begin with hyphens.")
                return await send_end(ctx, out)
            try:
                _ = int(args.newName)
                out.append("Updating name failed: Names cannot be numbers.")
                return await send_end(ctx, out)
            except ValueError:
                pass

            sql.template_kwarg_update(ctx.guild.id, orig_template.name, new_name=args.newName, date_modified=int(time.time()))
            out.append(f"Nickname changed from {name} to {args.newName}.")

        await send_end(ctx, out)

    # =======================
    #      Template Info
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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

    # =======================
    #     Template Remove
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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

    # =======================
    #    Template Snapshot
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template.group(name='snapshot', aliases=['s'], invoke_without_command=True, case_insensitive=True)
    async def template_snapshot(self, ctx, *filter):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            return await ctx.send(ctx.s("template.err.not_owner"))

        snapshots = [Snapshot(base, target) for base, target in sql.snapshots_get_all_by_guild(ctx.guild.id)]
        if not snapshots:
            return await ctx.send(f"No snapshots found, add some using `{ctx.gprefix}template snapshot add`")

        if filter:
            snapshots = [snapshot for i, snapshot in enumerate(snapshots) if snapshot.base.name in filter]

        for i, snap in enumerate(snapshots):
            snap_msg = await ctx.send(f"Checking {snap.target.name} for errors...")

            data = await http.get_template(snap.target.url, snap.target.name)
            fetch = self.bot.fetchers[snap.target.canvas]
            img = await fetch(snap.target.x, snap.target.y, snap.target.width, snap.target.height)
            func = partial(render.diff, snap.target.x, snap.target.y, data, 1, img, colors.by_name[snap.target.canvas])
            diff_img, tot, err, bad, _err, _bad = await self.bot.loop.run_in_executor(None, func)

            if not err:
                query = await utils.yes_no(ctx, "There are no errors on the snapshot, do you want to update it?", cancel=True)
                if query is False:
                    snap.result = "skip"
                    await snap_msg.delete(delay=1)
                    continue
                elif query is None:
                    for snap in snapshots[i:]:
                        snap.result = "cancel"
                    await snap_msg.delete(delay=1)
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
                    diff_msg = await ctx.send(content=out, file=f)

                query = await utils.yes_no(ctx, "There are errors on the snapshot, do you want to update it? You will loose track of progress if you do this.", cancel=True)
                if query is False:
                    snap.result = "err"
                    await snap_msg.delete(delay=1)
                    await diff_msg.delete(delay=1)
                    continue
                elif query is None:
                    for snap in snapshots[i:]:
                        snap.result = "cancel"
                    await snap_msg.delete(delay=1)
                    await diff_msg.delete(delay=1)
                    break

            await snap_msg.edit(content=f"Generating snapshot from {snap.base.name}...")

            data = await http.get_template(snap.base.url, snap.base.name)
            fetch = self.bot.fetchers[snap.base.canvas]
            img = await fetch(snap.base.x, snap.base.y, snap.base.width, snap.base.height)
            func = partial(
                render.diff, snap.base.x, snap.base.y, data, 1,
                img, colors.by_name[snap.base.canvas], create_snapshot=True)
            diff_img, tot, err, bad, _err, _bad = await self.bot.loop.run_in_executor(None, func)

            if not bad:
                with io.BytesIO() as bio:
                    diff_img.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, f"{snap.target.name}.png")
                    await snap_msg.delete(delay=1)
                    msg = await ctx.send(file=f)

                url = msg.attachments[0].url
                # The coordinates need to be casted to strings, or the regex in add_template will break shit
                result = await add_template(ctx, snap.base.canvas, snap.target.name, str(snap.base.x), str(snap.base.y), url)
                if result is None:
                    snap.result = "gen"
            else:
                snap.result = "bad"

        not_updated = [snap for snap in snapshots if snap.result is not None]

        if not_updated:
            out = []
            for snap in not_updated:
                reasons = {
                    "err": f"`{snap.base.name}`: errors on the current snapshot.",
                    "bad": f"`{snap.base.name}`: unquantised pixels detected.",
                    "cancel": f"`{snap.base.name}`: the command was cancelled.",
                    "skip": f"`{snap.base.name}`: the template was skipped.",
                    "gen": f"`{snap.base.name}`: template generation was halted."
                }
                text = reasons.get(snap.result)
                if text:
                    out.append(text)

            await ctx.send(
                embed=discord.Embed(description="Unupdated Snapshots").add_field(
                    name="name | reason",
                    value="\n".join(out)))

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
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
    @commands.cooldown(2, 5, commands.BucketType.guild)
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
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @template_snapshot.command(name='list', aliases=['l'])
    async def template_snapshot_list(self, ctx):
        snapshots = [Snapshot(base, target) for base, target in sql.snapshots_get_all_by_guild(ctx.guild.id)]
        if not snapshots:
            return await ctx.send(f"No snapshots found, add some using `{ctx.gprefix}template snapshot add`")

        snapshot_menu = menus.MenuPages(
            source=SnapshotSource(snapshots),
            clear_reactions_after=True,
            timeout=300.0)
        try:
            await snapshot_menu.start(ctx, wait=True)
            snapshot_menu.source.embed.set_footer(text=ctx.s("bot.timeout"))
            await snapshot_menu.message.edit(embed=snapshot_menu.source.embed)
        except discord.NotFound:
            await ctx.send(ctx.s("bot.menu_deleted"))
