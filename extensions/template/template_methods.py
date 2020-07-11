import asyncio
import hashlib
import io
import logging
import re
import time

import aiohttp
import discord
from PIL import Image

from objects import DbTemplate
from objects.errors import PilImageError, UrlError
import utils
from utils import canvases, colors, config, http, render, sqlite as sql

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
            return DbTemplate.new(ctx.guild.id, name, url, canvas, x, y, w, h, size, created, created, md5,
                                  ctx.author.id)
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
    if sql.template_count_by_guild_id(ctx.guild.id) >= config.MAX_TEMPLATES_PER_GUILD:
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
    log.info("(T:{} | X:{} | Y:{} | Dim:{})".format(t.name, t.x, t.y, t.size))
    name_chk = await check_for_duplicate_by_name(ctx, t)
    md5_chk = await check_for_duplicates_by_md5(ctx, t)

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

        sql.template_update(t)
        return await ctx.send(ctx.s("template.updated").format(name))

    if md5_chk is not None:
        dup_msg.insert(0, ctx.s("template.duplicate_list_open"))
        dup_msg.append(ctx.s("template.duplicate_list_close"))
        if await utils.yes_no(ctx, "\n".join(dup_msg)) is False:
            await ctx.send(ctx.s("template.menuclose"))
            return

    sql.template_add(t)
    return await ctx.send(ctx.s("template.added").format(name))


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


class Snapshot():
    def __init__(self, base, target):
        self.base = base
        self.target = target
        self.result = None


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
        w1 = max(max(map(lambda snap: len(snap.base.name), entries[offset:offset + self.per_page])) + 2, len("Base Template"))
        out = ["{0:<{w1}}  {1}".format("Base Template", "Snapshot Template", w1=w1)]

        for i, snap in enumerate(entries, start=offset):
            if i == offset + self.per_page:
                break
            out.append("{0.base.name:<{w1}}  {0.target.name}".format(snap, w1=w1))

        embed.add_field(
            name="Snapshots",
            value="```{0}``````{1}```".format(out[0], "\n".join(out[1:])),
            inline=False)
        self.embed = embed
        return embed
