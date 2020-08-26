import asyncio
import hashlib
import io
import logging
import re
import time

import aiohttp
import discord
from PIL import Image
import numpy as np
from sqlalchemy.sql import func

from lang import en_US
from objects.bot_objects import GlimContext
from objects.database_models import Template as TemplateDb
from objects.errors import PilImageError, UrlError
import utils
from utils import canvases, colors, config, converter, http, render

log = logging.getLogger(__name__)


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
                quantized = await check_colors(tmp, colors.by_name[canvas])
            if not quantized:
                if await utils.yes_no(ctx, ctx.s("template.not_quantized")) is False:
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

            return TemplateDb(
                guild_id=ctx.guild.id, name=name, url=url, canvas=canvas,
                x=x, y=y, width=w, height=h, size=size, date_added=created,
                date_modified=created, md5=md5, owner=ctx.author.id
            )
    except aiohttp.client_exceptions.InvalidURL:
        raise UrlError
    except IOError:
        raise PilImageError


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
    if name[0] == "-":
        await ctx.send("Template names cannot begin with hyphens.")
        return
    try:
        _ = int(name)
        await ctx.send("Template names cannot be numbers.")
        return
    except ValueError:
        pass

    if ctx.session.query(func.count(TemplateDb.id)).filter_by(
            guild_id=ctx.guild.id).scalar() >= config.MAX_TEMPLATES_PER_GUILD:
        await ctx.send(ctx.s("template.err.max_templates"))
        return
    url = await select_url(ctx, url)
    if url is None:
        await ctx.send(ctx.s("template.err.no_image"))
        return
    try:
        # Removes all spaces and chars that aren't 0-9 or the minus sign.
        x = int(re.sub('[^0-9-]', '', x))
        y = int(re.sub('[^0-9-]', '', y))
    except ValueError:
        await ctx.send(ctx.s("template.err.invalid_coords"))
        return

    t = await build_template(ctx, name, x, y, url, canvas)
    if not t:
        await ctx.send(ctx.s("template.err.template_gen_error"))
        return

    name_chk = await check_for_duplicate_by_name(ctx, t.name)
    md5_chk = await check_for_duplicates_by_md5(ctx, t.md5)

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
            await ctx.send(ctx.s("template.menuclose"))
            return

        # Update template
        ctx.session.query(TemplateDb)\
            .filter_by(guild_id=ctx.guild.id, name=name)\
            .update({
                "url": t.url,
                "canvas": t.canvas,
                "x": t.x,
                "y": t.y,
                "width": t.width,
                "height": t.height,
                "size": t.size,
                "date_modified": t.date_modified,
                "md5": t.md5,
                "owner": t.owner
            })
        ctx.session.commit()
        return await ctx.send(ctx.s("template.updated").format(name))

    if md5_chk is not None:
        dup_msg.insert(0, ctx.s("template.duplicate_list_open"))
        dup_msg.append(ctx.s("template.duplicate_list_close"))
        if await utils.yes_no(ctx, "\n".join(dup_msg)) is False:
            await ctx.send(ctx.s("template.menuclose"))
            return

    ctx.session.add(t)
    ctx.session.commit()
    return await ctx.send(ctx.s("template.added").format(name))


async def check_for_duplicates_by_md5(ctx, md5):
    """Checks for duplicates using md5 hashing, returns the list of duplicates if any exist.

    Arguments:
    ctx - commands.Context object.
    template - A template object.

    Returns:
    A list or nothing.
    """
    dups = ctx.session.query(TemplateDb).filter_by(
        guild_id=ctx.guild.id, md5=md5).all()
    return dups if len(dups) > 0 else None


async def check_for_duplicate_by_name(ctx, name):
    """Checks for duplicates by name, returns a that template if one exists and the user has
    permission to overwrite, False if they do not. None is returned if no other templates share
    this name.

    Arguments:
    ctx - commands.Context.
    name - Template name.

    Returns:
    A template object, False or None.
    """
    dup = ctx.session.query(TemplateDb).filter_by(
        guild_id=ctx.guild.id, name=name).first()
    if dup:
        if dup.owner != ctx.author.id and not utils.is_admin(ctx):
            await ctx.send(ctx.s("template.err.name_exists"))
            return False
        return dup


async def send_end(ctx, out):
    if out != []:
        await ctx.send(embed=discord.Embed(description="Template updated!").add_field(
            name="Summary of changes",
            value="```{}```".format("\n".join(out))))
    else:
        await ctx.send("Template not updated as no arguments were provided.")


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
        await send_end(ctx, out)
        return

    # there was no url in the text of the message, is there an attachment?
    if len(ctx.message.attachments) > 0:
        return ctx.message.attachments[0].url

    out.append("Updating image failed, no attachments could be detected.")
    await send_end(ctx, out)
    return


class TemplateSource(discord.ext.menus.ListPageSource):
    def __init__(self, data):
        super().__init__(data, per_page=10)
        self.embed = None

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
                value="[{0}, {1}](https://pixelcanvas.io/@{0},{1}) | [Link to file]({2})".format(
                    template.x, template.y, template.url),
                inline=False)
        self.embed = embed
        return embed


class SnapshotSource(discord.ext.menus.ListPageSource):
    def __init__(self, data):
        super().__init__(data, per_page=10)
        self.embed = None

    async def format_page(self, menu, entries):
        embed = discord.Embed(
            description=f"Page {menu.current_page + 1} of {self.get_max_pages()}")
        embed.set_footer(
            text="Scroll using the reactions below to see other pages.")

        offset = menu.current_page * self.per_page
        # Find which is longest: the title or one of the template names. Set w1 to that + 2.
        # Then w1 can be used to offset text so that the start of the second column all lines up.
        w1 = max(max(map(lambda snap: len(snap.base_template.name), entries[offset:offset + self.per_page])) + 2, len("Base Template"))
        out = ["{0:<{w1}}  {1}".format("Base Template", "Snapshot Template", w1=w1)]

        for i, snap in enumerate(entries, start=offset):
            if i == offset + self.per_page:
                break
            out.append("{0.base_template.name:<{w1}}  {0.target_template.name}".format(snap, w1=w1))

        embed.add_field(
            name="Snapshots",
            value="```{0}``````{1}```".format(out[0], "\n".join(out[1:])),
            inline=False)
        self.embed = embed
        return embed


class CheckerSource(discord.ext.menus.ListPageSource):
    def __init__(self, pixels, templates):
        super().__init__(pixels, per_page=10)
        self.embed = None
        self.templates = templates

    async def format_page(self, menu, entries):
        embed = discord.Embed(
            description=f"Page {menu.current_page + 1} of {self.get_max_pages()}")
        embed.set_footer(
            text="Scroll using the reactions below to see other pages.")

        colors = []
        for x in range(16):
            colors.append(menu.ctx.s(f"color.pixelcanvas.{x}"))

        offset = menu.current_page * self.per_page
        for i, p in enumerate(entries, start=offset):
            if i == offset + self.per_page:
                break

            try:
                template = [t for t in self.templates if t.id == p.template_id][0]
            except IndexError:
                continue

            try:
                template_color = colors[int(template.array[abs(p.x - template.sx), abs(p.y - template.sy)])]
            except IndexError:
                continue

            s = round(time.time() - p.recieved)
            h = s // 3600
            s -= h * 3600
            m = s // 60
            s -= m * 60
            delta = f"{h}h" if h != 0 else ""
            delta += f"{m}m" if m != 0 else ""
            delta += f"{s}s" if s != 0 else ""

            embed.add_field(
                name=f"{delta} ago - Template: {template.name}",
                value="[@{0.x},{0.y}](https://pixelcanvas.io/@{0.x},{0.y}) is **{1}**, should be **{2}**.".format(
                    p, colors[p.damage_color], template_color),
                inline=False)

        self.embed = embed
        return embed


class Pixel:
    colors = []
    for x in range(16):
        colors.append(en_US.STRINGS.get(f"color.pixelcanvas.{x}", None))

    def __init__(self, damage_color, x, y, alert_id, template_id):
        self.damage_color = damage_color  # int from 0-15, corresponds to an index in colors
        self.x = x
        self.y = y
        self.alert_id = alert_id  # Alert message this pixel is for
        self.template_id = template_id
        self.recieved = time.time()
        self.fixed = False

    def __repr__(self):
        return ("Pixel(color={0.log_color}, x={0.x}, y={0.y}, aid={0.alert_id}, tid={0.template_id}, "
                "recieved={0.recieved}, fixed={0.fixed})".format(self))

    @property
    def log_color(self):
        return Pixel.colors[self.damage_color]


class Template:
    def __init__(self,
                 id: int, name: str, array: np.array, url: str, md5: str, sx: int, sy: int,
                 alert_channel: int, gid):

        self.gid = gid
        self.id = id  # Unique identifier for each template, never changes, sourced from db
        self.name = name
        self.array = array  # Image array
        self.url = url  # Image url
        self.md5 = md5

        self.sx, self.sy = sx, sy  # sx = start x, ex = end x
        self.ex, self.ey = sx + array.shape[0], sy + array.shape[1]

        self.alert_channel = alert_channel  # Channel id
        self.last_alert_message = None  # Message object
        self.sending = False

        self.pixels = []

    def __repr__(self):
        return ("Template(id={0.id}, gid={0.gid}, name={0.name}, url={0.url}, md5={0.md5}, sx={0.sx}, sy={0.sy} "
                "ex={0.ex}, ey={0.ey}, aid={0.alert_channel}, message={0.last_alert_message}, sending={0.sending}, "
                "pixels={0.pixels})".format(self))

    @property
    def current_pixels(self):
        if self.last_alert_message:
            return [p for p in self.pixels if p.alert_id == self.last_alert_message.id]

    @staticmethod
    async def new(t):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(t.url) as resp:
                if resp.status == 200:
                    image = Image.open(io.BytesIO(await resp.read())).convert('RGBA')
                else:
                    # Skip this template, it can get updated on the next round, no point retrying and delaying the others
                    log.exception(f"File for {t.name} could not be downloaded, status code: {resp.status}")
                    return None

        template = Template(t.id, t.name, converter.image_to_array(image, "pixelcanvas"), t.url, t.md5,
                            t.x, t.y, t.alert_id, t.guild_id)
        log.debug(f"Generated {template}.")
        return template

    def s(self, str_id):
        return GlimContext.get_from_guild(self.gid, str_id)

    def color_string(self, index):
        return self.s(f"color.pixelcanvas.{index}")

    def in_range(self, x, y):
        return self.sx <= x < self.ex and self.sy <= y < self.ey

    def changed(self, t_db):
        return (t_db.x != self.sx) or \
               (t_db.y != self.sy) or \
               (t_db.md5 != self.md5) or \
               (t_db.name != self.name) or \
               (t_db.alert_id != self.alert_channel)

    def color_at(self, x, y):
        return int(self.array[abs(x - self.sx), abs(y - self.sy)])

    def add_pixel(self, color, x, y):
        try:
            alert_id = self.last_alert_message.id
        except AttributeError:
            alert_id = "flag"
        pixel = Pixel(color, x, y, alert_id, self.id)
        self.pixels.append(pixel)
        return pixel
