import datetime
from functools import partial
import io
import logging
import math
import re
import time

import discord
from discord.ext import commands, menus

from extensions.template.utils import \
    (build_template,
     add_template,
     send_end,
     select_url_update,
     TemplateSource,
     SnapshotSource)
from objects.database_models import \
    (Guild,
     Template as TemplateDb,
     Snapshot)
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
     FactionAction)

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

        templates = ctx.session.query(TemplateDb).filter_by(guild_id=gid).all()
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
        unhidden_guild_ids = ctx.session.query(Guild.id).filter(
            Guild.faction_name != None,
            Guild.faction_hidden == False
        ).subquery()

        unhidden_templates = ctx.session.query(TemplateDb).filter(
            TemplateDb.guild_id.in_(unhidden_guild_ids))
        ts = unhidden_templates.order_by(
            TemplateDb.guild_id.desc(),
            TemplateDb.canvas.asc(),
            TemplateDb.name.asc()
        ).all()

        ts = sorted(ts, key=lambda t: t.guild.faction_name)

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
            for t, f in ts[(page - 1) * 10:page * 10]:
                coords = "{}, {}".format(t.x, t.y)
                faction = '"{}"'.format(f)
                name = '"{}"'.format(t.name)
                canvas_name = canvases.pretty_print[t.canvas]
                msg.append("{0:<{w1}}  {1:<34}  {2:<14}  {3}".format(name, faction, canvas_name, coords, w1=w1))
            msg.append("")
            msg.append("// " + ctx.s("template.list_all_footer_1").format(ctx.prefix))
            msg.append("// " + ctx.s("template.list_all_footer_2").format(ctx.prefix))
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

        orig_template = ctx.session.query(TemplateDb).filter_by(
            guild_id=ctx.guild.id, name=name).first()
        if not orig_template:
            raise TemplateNotFoundError(ctx, ctx.guild.id, name)

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

            # update template data
            orig_template.url = t.url
            orig_template.md5 = t.md5
            orig_template.width = t.width
            orig_template.height = t.height
            orig_template.size = t.size
            orig_template.date_modified = t.date_modified
            out.append("File updated.")

        if args.x:
            try:
                x = int(re.sub('[^0-9-]', '', args.x))
            except ValueError:
                out.append("Updating x failed, value provided was not a number.")
                return await send_end(ctx, out)

            orig_template.x = x
            orig_template.date_modified = int(time.time())
            out.append(f"X coordinate changed from {orig_template.x} to {x}.")

        if args.y:
            try:
                y = int(re.sub('[^0-9-]', '', args.y))
            except ValueError:
                out.append("Updating y failed, value provided was not a number.")
                return await send_end(ctx, out)

            orig_template.y = y
            orig_template.date_modified = int(time.time())
            out.append(f"Y coordinate changed from {orig_template.y} to {y}.")

        if args.newName:
            dup_check = ctx.session.query(TemplateDb.name).filter_by(
                guild_id=ctx.guild.id, name=args.newName).first()
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

            orig_template.name = args.newName
            orig_template.date_modified = int(time.time())
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
            gid, faction = ctx.guild.id, ctx.session.query(Guild).get(ctx.guild.id)

        t = ctx.session.query(TemplateDb).filter_by(guild_id=gid, name=name).first()
        if not t:
            raise TemplateNotFoundError(ctx, gid, name)

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

        if t.alert_id:
            channel = self.bot.get_channel(t.alert_id)
            e.add_field(name=ctx.s("bot.alert_channel"), value=channel.mention, inline=True)

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
        t = ctx.session.query(TemplateDb).filter_by(guild_id=ctx.guild.id, name=name).first()
        if not t:
            raise TemplateNotFoundError(ctx, ctx.guild.id, name)
        log.info("(T:{} G:{})".format(t.name, t.guild_id))
        if t.owner_id != ctx.author.id and not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.not_owner"))
            return
        ctx.session.delete(t)
        await ctx.send(ctx.s("template.remove").format(name))

    # =======================
    #        Snapshot
    # =======================

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @commands.group(name='snapshot', aliases=['s'], invoke_without_command=True, case_insensitive=True)
    async def snapshot(self, ctx, *filter):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            return await ctx.send(ctx.s("template.err.not_owner"))

        snapshots = ctx.session.query(Snapshot).filter(
            Snapshot.base_template.guild_id == ctx.guild.id)

        if filter:
            snapshots = snapshots.filter(Snapshot.base_template.name.in_(filter)).all()
        else:
            snapshots = snapshots.all()

        if not snapshots:
            return await ctx.send(f"No snapshots found, add some using `{ctx.prefix}template snapshot add`")

        for i, snap in enumerate(snapshots):
            snap_msg = await ctx.send(f"Checking {snap.target_template.name} for errors...")

            data = await http.get_template(snap.target_template.url, snap.target_template.name)
            fetch = self.bot.fetchers[snap.target_template.canvas]
            img = await fetch(snap.target_template.x, snap.target_template.y, snap.target_template.width, snap.target_template.height)
            func = partial(render.diff, snap.target_template.x, snap.target_template.y, data, 1, img, colors.by_name[snap.target_template.canvas])
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

            await snap_msg.edit(content=f"Generating snapshot from {snap.base_template.name}...")

            data = await http.get_template(snap.base_template.url, snap.base_template.name)
            fetch = self.bot.fetchers[snap.base_template.canvas]
            img = await fetch(snap.base_template.x, snap.base_template.y, snap.base_template.width, snap.base_template.height)
            func = partial(
                render.diff, snap.base_template.x, snap.base_template.y, data, 1,
                img, colors.by_name[snap.base_template.canvas], create_snapshot=True)
            diff_img, tot, err, bad, _err, _bad = await self.bot.loop.run_in_executor(None, func)

            if not bad:
                with io.BytesIO() as bio:
                    diff_img.save(bio, format="PNG")
                    bio.seek(0)
                    f = discord.File(bio, f"{snap.target_template.name}.png")
                    await snap_msg.delete(delay=1)
                    msg = await ctx.send(file=f)

                url = msg.attachments[0].url
                # The coordinates need to be casted to strings, or the regex in add_template will break shit
                result = await add_template(ctx, snap.base_template.canvas, snap.target_template.name, str(snap.base_template.x), str(snap.base_template.y), url)
                if result is None:
                    snap.result = "gen"
            else:
                snap.result = "bad"

        not_updated = [snap for snap in snapshots if snap.result is not None]

        if not_updated:
            out = []
            for snap in not_updated:
                reasons = {
                    "err": f"`{snap.base_template.name}`: errors on the current snapshot.",
                    "bad": f"`{snap.base_template.name}`: unquantised pixels detected.",
                    "cancel": f"`{snap.base_template.name}`: the command was cancelled.",
                    "skip": f"`{snap.base_template.name}`: the template was skipped.",
                    "gen": f"`{snap.base_template.name}`: template generation was halted."
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
    @snapshot.command(name='add', aliases=['a'])
    async def snapshot_add(self, ctx, base_template, snapshot_template):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.not_owner"))
            return

        base = ctx.session.query(TemplateDb).filter_by(guild_id=ctx.guild.id, name=base_template).first()
        target = ctx.session.query(TemplateDb).filter_by(guild_id=ctx.guild.id, name=snapshot_template).first()

        if base is None:
            return await ctx.send("The base template does not exist.")
        if target is None:
            return await ctx.send("The snapshot template does not exist.")

        snap = Snapshot(base_template=base, target_template=target)
        ctx.session.add(snap)
        await ctx.send("Snapshot added!")

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @snapshot.command(name='remove', aliases=['r'])
    async def snapshot_remove(self, ctx, base_template, snapshot_template):
        if not utils.is_template_admin(ctx) and not utils.is_admin(ctx):
            return await ctx.send(ctx.s("template.err.not_owner"))

        snap = ctx.session.query(Snapshot).filter(
            Snapshot.base_template.name == base_template,
            Snapshot.target_template.name == snapshot_template
        ).first()

        if not snap:
            return await ctx.send("That snapshot does not exist.")

        ctx.session.delete(snap)
        await ctx.send("Snapshot removed!")

    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.guild)
    @checks.template_adder_only()
    @snapshot.command(name='list', aliases=['l'])
    async def snapshot_list(self, ctx):
        snapshots = ctx.session.query(Snapshot).filter(
            Snapshot.base_template.guild_id == ctx.guild.id).all()
        if not snapshots:
            return await ctx.send(f"No snapshots found, add some using `{ctx.prefix}template snapshot add`")

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
