from functools import partial
import logging
import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageOps
import hitherdither

from objects import Coords
from objects.chunks import BigChunk, ChunkPz, PxlsBoard
from utils import colors, http, config, yliluoma2

log = logging.getLogger(__name__)


async def calculate_size(data):
    """Calculates the number of non-transparent pixels there are in an image.

    Arguments:
    data - A bytestream of an image.

    Returns:
    The number of non-transparent pixels, integer.
    """
    template = Image.open(data).convert('RGBA')
    alpha = Image.new('RGBA', template.size, (0, 0, 0, 0))
    white = Image.new('RGBA', template.size, (255, 255, 255, 255))
    white = Image.composite(white, alpha, template)
    return int(np.array(white).any(axis=-1).sum())


def dither(origImg, canvas_palette, type=None, threshold=8, order=4):
    # find all fully transparent pixels
    alpha_mask = origImg.split()[3]
    alpha_mask = Image.eval(alpha_mask, lambda a: 255 if a == 0 else 0)
    origImg = origImg.convert('RGB')

    palette = hitherdither.palette.Palette(canvas_palette)
    yliluoma = yliluoma2.Yliluoma(order, canvas_palette)
    dithers = {
        "yliluoma": partial(yliluoma.dither, origImg),
        "bayer": partial(hitherdither.ordered.bayer.bayer_dithering, origImg, palette, [threshold / 4], order),
        "floyd-steinberg": partial(hitherdither.diffusion.error_diffusion_dithering, origImg, palette, "floyd-steinberg", order)
    }

    dither_func = dithers.get(type)
    dithered_image = dither_func()
    dithered_image = Image.composite(Image.new('RGBA', origImg.size, (0, 0, 0, 0)), dithered_image.convert('RGBA'), alpha_mask)
    return dithered_image


def diff(x, y, data, zoom, diff_img, palette, create_snapshot=False, highlight_correct=False, color_blind=False):
    """Calculates and renders a diff image.

    Arguments:
    x - The x coord, integer.
    y - The y coord, integer.
    data - The image, a bytestream.
    zoom - The factor to zoom by, integer.
    diff_img - A PIL Image object of the area of canvas needed.
    palette - The palette to use, a list of rgb tuples.
    Kwargs:
    create_snapshot - If a "finished template" should be made, where the only non-transparent pixels are those that are correct on canvas right now, bool
    highlight_correct - If correct pixels should be highlighted in green, bool
    color_blind - If the renders should be color blind friendly, bool

    Returns:
    diff_img - The rendered image, a PIL Image object.
    tot - The total number of pixels in this image not including transparency, integer.
    err - The total number of errors, integer.
    bad - The total number of off-palette pixels, integer.
    error_list - A list of errors, each error being a tuple like (pixel, is_colour, should_be_colour).
    bad_list - A list of bad-pixel colours, each being a list like [(r, g, b), number_of_occurances].
    """
    with data:
        template = Image.open(data).convert('RGBA')

    with template:
        log.info("(X:{0} | Y:{1} | Dim:{2}x{3} | Z:{4})".format(x, y, template.width, template.height, zoom))

        black = Image.new('1', template.size, 0)
        white = Image.new('1', template.size, 1)
        mask = Image.composite(white, black, template)
        template_copy = template.copy()
        template = template.convert('RGB')

        def lut(i):
            return 255 if i > 0 else 0

        with ImageChops.difference(template, diff_img) as error_mask:
            error_mask = error_mask.point(lut).convert('L').point(lut).convert('1')
            error_mask = Image.composite(error_mask, black, mask)

        if highlight_correct:
            _r, _g, _b, template_mask = template_copy.split()
            with ImageChops.difference(template_mask, error_mask) as template_mask:
                template_mask = template_mask.point(lut).convert('L').point(lut).convert('1')
                template_mask = Image.composite(template_mask, black, mask)

        with ImageChops.difference(template, _quantize(template, palette)) as bad_mask:
            bad_mask = bad_mask.point(lut).convert('L').point(lut).convert('1')
            bad_mask = Image.composite(bad_mask, black, mask)

        tot = np.array(mask).sum()
        err = np.array(error_mask).sum()
        bad = np.array(bad_mask).sum()
        errors = np.argwhere(np.array(error_mask)).tolist()
        bad_pixels = np.argwhere(np.array(bad_mask)).tolist()

        error_list = []
        for p in errors:
            p.reverse()  # NumPy is backwards
            try:
                t_color = palette.index(template.getpixel(tuple(p)))
            except ValueError:
                t_color = None
            try:
                f_color = palette.index(diff_img.getpixel(tuple(p)))
            except ValueError:
                f_color = None

            if f_color is None or t_color is None:
                continue

            error_list.append((*p, f_color, t_color))

        for p in bad_pixels:
            p.reverse()
        bad_list = [template.getpixel(tuple(p)) for p in bad_pixels]
        bad_dict = dict.fromkeys(bad_list, 0)
        for color in bad_list:
            bad_dict[color] += 1
        bad_list = [(key, value) for key, value in bad_dict.items()]
        bad_list = sorted(bad_list, key=lambda n: n[1], reverse=True)  # Sort by n.o. occurances, high to low

        for x in range(diff_img.width):
            for y in range(diff_img.height):
                if diff_img.getpixel((x, y)) == (34, 34, 34):
                    diff_img.putpixel((x, y), (0, 0, 0))

        if create_snapshot:
            # Make a snapshot
            diff_img = template_copy
            diff_img.paste(Image.new('RGBA', template.size, (0, 0, 0, 0)), mask=error_mask)
        else:
            # Make a normal diff
            diff_img_mask = diff_img.copy().convert('L')
            diff_img = diff_img_mask.copy().convert('RGB')

            if highlight_correct:
                # Highlight both correct and incorrect pixels
                correct_color_light, correct_color_dark = ((87, 191, 71), (6, 36, 1)) if not color_blind else ((36, 89, 249), (8, 19, 82))
                bad_color_light, bad_color_dark = ((36, 89, 249), (8, 19, 82)) if not color_blind else ((200, 71, 216), (58, 8, 82))

                correct_color_light = Image.new('RGB', template.size, correct_color_light)
                correct_color_dark = Image.new('RGB', template.size, correct_color_dark)
                incorrect_color_light = Image.new('RGB', template.size, (230, 60, 60))
                incorrect_color_dark = Image.new('RGB', template.size, (82, 8, 8))
                bad_color_light = Image.new('RGB', template.size, bad_color_light)
                bad_color_dark = Image.new('RGB', template.size, bad_color_dark)

                correct_img = Image.composite(correct_color_light, correct_color_dark, diff_img_mask)
                incorrect_img = Image.composite(incorrect_color_light, incorrect_color_dark, diff_img_mask)
                bad_img = Image.composite(bad_color_light, bad_color_dark, diff_img_mask)

                diff_img.paste(correct_img, mask=template_mask)
                diff_img.paste(incorrect_img, mask=error_mask)
                diff_img.paste(bad_img, mask=bad_mask)
            else:
                diff_img.paste(Image.new('RGB', template.size, (255, 0, 0)), mask=error_mask)
                diff_img.paste(Image.new('RGB', template.size, (0, 0, 255)), mask=bad_mask)

            if zoom > 1:
                diff_img = diff_img.resize(tuple(zoom * x for x in diff_img.size), Image.NEAREST)

    return diff_img, tot, err, bad, error_list, bad_list


async def preview(x, y, zoom, fetch):
    """Get a preview image from coordinates.

    Arguments:
    x - The x coord, integer.
    y - The y coord, integer.
    zoom - The factor to zoom by, integer.
    fetch - A fetching function.

    Returns:
    The preview, as a PIL Image object.
    """
    log.info("(X:{0} | Y:{1} | Zoom:{2})".format(x, y, zoom))

    dim = Coords(config.PREVIEW_W, config.PREVIEW_H)
    if zoom < -1:
        dim *= abs(zoom)

    preview_img = await fetch(x - dim.x // 2, y - dim.y // 2, *dim)
    if zoom > 1:
        preview_img = preview_img.resize(tuple(zoom * x for x in preview_img.size), Image.NEAREST)
        tlp = Coords(preview_img.width // 2 - config.PREVIEW_W // 2, preview_img.height // 2 - config.PREVIEW_H // 2)
        preview_img = preview_img.crop((*tlp, tlp.x + config.PREVIEW_W, tlp.y + config.PREVIEW_H))

    if config.INVERT:
        preview_img = ImageOps.invert(preview_img)

    return preview_img


async def preview_template(t, zoom, fetch):
    """Get a preview image from a template.

    Arguments:
    t - A template object.
    zoom - The factor to zoom by, integer.
    fetch - A fetching function.

    Returns:
    The preview, as a PIL Image object.
    """
    log.info("(X:{0} | Y:{1} | Dim:{2}x{3} | Zoom:{4})".format(t.x, t.y, t.width, t.height, zoom))

    dim = Coords(t.width, t.height)
    if zoom < -1:
        dim *= abs(zoom)

    c = Coords(*t.center)

    preview_img = await fetch(c.x - dim.x // 2, c.y - dim.y // 2, *dim)
    if zoom > 1:
        preview_img = preview_img.resize(tuple(zoom * x for x in preview_img.size), Image.NEAREST)
        tlp = Coords(preview_img.width // 2 - config.PREVIEW_W // 2, preview_img.height // 2 - config.PREVIEW_H // 2)
        preview_img = preview_img.crop((*tlp, tlp.x + config.PREVIEW_W, tlp.y + config.PREVIEW_H))

    return preview_img


def quantize(data, palette):
    """Quantizes an image.

    Arguments:
    data - An image as a bytestream.
    palette - The palette to quantize it with, a list of rgb tuples.

    Returns:
    q - The rendered quantized image, a PIL Image object.
    bad_pixels - A numpy array of every pixel that is off palette.
    """
    with data:
        template = Image.open(data).convert('RGBA')

    log.info("(Dim:{0}x{1})".format(template.width, template.height))

    black = Image.new('1', template.size, 0)
    white = Image.new('1', template.size, 1)
    mask = Image.composite(white, black, template)
    template = template.convert('RGB')
    q = _quantize(template, palette)

    def lut(i):
        return 255 if i > 0 else 0

    with ImageChops.difference(template, q) as d:
        d = d.point(lut).convert('L').point(lut).convert('1')
        d = Image.composite(d, black, mask)
        bad_pixels = np.array(d).sum()

    alpha = Image.new('RGBA', template.size, (0, 0, 0, 0))
    q = Image.composite(q.convert('RGBA'), alpha, mask)

    return q, bad_pixels


def gridify(data, color, zoom):
    """Make a gridified version of an image.

    Arguments:
    data - The image as a bytestream.
    color - The colour to make the grid lines, hexadecimal value.
    zoom - The factor to zoom by, integer.

    Returns:
    template - The gridified image, a PIL Image object.
    """
    color = (color >> 16 & 255, color >> 8 & 255, color & 255, 255)
    zoom += 1
    with data:
        template = Image.open(data).convert('RGBA')
        log.info("(Dim:{0}x{1} | Zoom:{2})".format(template.width, template.height, zoom))
        template = template.resize((template.width * zoom, template.height * zoom), Image.NEAREST)
        draw = ImageDraw.Draw(template)
        for i in range(1, template.height):
            draw.line((0, i * zoom, template.width, i * zoom), fill=color)
        for i in range(1, template.width):
            draw.line((i * zoom, 0, i * zoom, template.height), fill=color)
        del draw
        return template


def zoom(data, zoom):
    """Zooms an image by a given factor.

    Arguments:
    data - The image, a bytestream.
    zoom - The factor to zoom by, integer.

    Returns:
    A PIL Image object.
    """
    with data:
        template = Image.open(data).convert('RGBA')
        log.info("(Dim:{0}x{1} | Zoom:{2})".format(template.width, template.height, zoom))
        template = template.resize((template.width * zoom, template.height * zoom), Image.NEAREST)
        return template


async def fetch_pixelcanvas(x, y, dx, dy):
    """Fetches the current state of a given section of pixelcanvas.

    Arguments:
    x - The x coordinate, integer.
    y - The y coordinate, integer.
    dx - The width, integer.
    dy - The height, integer.

    Returns:
    A PIL Image object of the area requested.
    """
    bigchunks, shape = BigChunk.get_intersecting(x, y, dx, dy)
    fetched = Image.new('RGB', tuple([960 * x for x in shape]), colors.pixelcanvas[1])

    await http.fetch_chunks(bigchunks)

    for i, bc in enumerate(bigchunks):
        if bc.is_in_bounds():
            fetched.paste(bc.image, ((i % shape[0]) * 960, (i // shape[0]) * 960))

    x, y = x - (x + 448) // 960 * 960 + 448, y - (y + 448) // 960 * 960 + 448
    return fetched.crop((x, y, x + dx, y + dy))


async def fetch_pixelzone(x, y, dx, dy):
    """Fetches the current state of a given section of pixelzone.

    Arguments:
    x - The x coordinate, integer.
    y - The y coordinate, integer.
    dx - The width, integer.
    dy - The height, integer.

    Returns:
    A PIL Image object of the area requested.
    """
    chunks, shape = ChunkPz.get_intersecting(x, y, dx, dy)
    fetched = Image.new('RGB', tuple([512 * x for x in shape]), colors.pixelzone[2])

    await http.fetch_chunks(chunks)

    for i, ch in enumerate(chunks):
        if ch.is_in_bounds():
            fetched.paste(ch.image, ((i % shape[0]) * 512, (i // shape[0]) * 512))

    return fetched.crop((x % 512, y % 512, (x % 512) + dx, (y % 512) + dy))


async def fetch_pxlsspace(x, y, dx, dy):
    """Fetches the current state of a given section of pxlsspace.

    Arguments:
    x - The x coordinate, integer.
    y - The y coordinate, integer.
    dx - The width, integer.
    dy - The height, integer.

    Returns:
    A PIL Image object of the area requested.
    """
    board = PxlsBoard()
    fetched = Image.new('RGB', (dx, dy), colors.pxlsspace[1])
    await http.fetch_chunks([board])
    fetched.paste(board.image, (-x, -y, board.width - x, board.height - y))
    return fetched


def _quantize(t: Image, palette) -> Image:
    with Image.new('P', (1, 1)) as palette_img:
        # Flatten 2d array to 1d, then pad with first color to 786 total values
        p = [v for color in palette for v in color] + list(palette[0]) * (256 - len(palette))
        palette_img.putpalette(p)
        palette_img.load()
        im = t.im.convert('P', 0, palette_img.im)  # Quantize using internal PIL shit so it's not dithered
        return t._new(im).convert('RGB')
