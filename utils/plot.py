from io import BytesIO, StringIO
import uuid
import copy
import csv
import math

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.cm import get_cmap
from matplotlib.colors import ListedColormap
import numpy as np
from sqlalchemy import func
from sqlalchemy.sql import select, insert, and_

from objects.database_models import Canvas, Pixel, Online, temp_table, engine
from objects.errors import NotEnoughDataError
from utils import colors

# NOTE: On width choice for bar charts where the x axis is datetime:
# Bar chart width for datetimes is appx width of 1 = 1 day
# (https://github.com/matplotlib/matplotlib/issues/13236#issuecomment-457394944), so...
# When each bar is an hour:
# 1/24 = 0.041
# 0.041 * 0.8 = 0.033


def format_date(ctx, ax, duration):
    hrs = range(24)
    if duration.hours > 12:
        hrs = [6, 12, 18]

    d = matplotlib.dates.DayLocator()
    h = matplotlib.dates.HourLocator(byhour=hrs)
    day_formatter = matplotlib.dates.DateFormatter("%d/%m/%Y" if duration.days <= 4 else "%d/%m/%y")
    hour_formatter = matplotlib.dates.DateFormatter("%I%p")

    if duration.days <= 4:
        ax.xaxis.set_minor_formatter(hour_formatter)
        ax.xaxis.set_tick_params(which='minor', labelsize=7.0)

    ax.xaxis.set_minor_locator(h)
    ax.xaxis.set_major_locator(d)
    ax.xaxis.set_major_formatter(day_formatter)
    ax.xaxis.set_tick_params(which='major', pad=10, labelsize=9.0)
    ax.set_xlim(duration.start, duration.end)
    ax.set_xlabel(ctx.s("bot.time_label"))


def create_fig():
    # Creating the figure this way because plt isn't garbage collected (It should be, what the fuck matplotlib)
    fig = Figure()
    _ = FigureCanvasAgg(fig)
    return fig


def save_fig(fig):
    buf = BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return buf


def alert_comparision(ctx, x_data, ally_values, enemy_values, duration):
    fig = create_fig()
    ax_1, ax_2 = fig.subplots(2, sharex=True, sharey=True)

    ax_1.bar(x_data, ally_values, width=0.033, color="green")
    ax_2.bar(x_data, enemy_values, width=0.033, color="red")

    ax_1.grid(True)
    ax_2.grid(True)
    ax_1.set_axisbelow(True)
    ax_2.set_axisbelow(True)
    ax_1.set_title(ctx.s("alerts.allies"))
    ax_2.set_title(ctx.s("alerts.enemies"))
    ax_1.set_ylabel(ctx.s("alerts.comparision_y_label"))
    ax_2.set_ylabel(ctx.s("alerts.comparision_y_label"))
    format_date(ctx, ax_2, duration)
    ax_2.set_ylim(bottom=0)

    return save_fig(fig)


def alert_gain(ctx, x_data, ally_values, enemy_values, duration):
    fig = create_fig()
    ax = fig.subplots()

    gain_values = np.subtract(ally_values, enemy_values)
    gain_colors = np.where(gain_values < 0, "r", "g")

    ax.bar(x_data, gain_values, width=0.033, color=gain_colors)

    ax.grid(True)
    ax.set_axisbelow(True)
    ax.set_ylabel(ctx.s("alerts.gain_y_label"))
    format_date(ctx, ax, duration)

    # Center y axis at zero
    bottom, top = ax.get_ylim()
    biggest = max(abs(bottom), abs(top))
    ax.set_ylim(bottom=0 - biggest, top=biggest)

    return save_fig(fig)


def process_color_pie(ctx, canvas, start, end):
    sq = ctx.session.query(Canvas.id).filter_by(nick=canvas)
    q = ctx.session.query(Pixel.color, func.count("(*)"))
    q = q.filter(
        Pixel.placed.between(start, end),
        Pixel.canvas_id.in_(sq))
    data = q.group_by(Pixel.color).all()

    if not len(data):
        raise NotEnoughDataError

    return data


def color_pie(data, canvas):
    fig = create_fig()
    ax = fig.subplots()

    data.sort(key=lambda d: d[1])
    x_values = [count for c, count in data]

    palette = colors.by_name[canvas]
    color_values = ["#{0:02x}{1:02x}{2:02x}".format(*palette[c]) for c, count in data]

    ax.pie(x_values, colors=color_values)
    ax.axis('equal')

    return save_fig(fig)


def process_histogram(canvas, start, end, axes):
    start_x, end_x, start_y, end_y = axes

    # Create temp table to insert the results of our query, so we can safely fetch
    # it in chunks. If we did it on the live table the order_by would screw things up
    temp_pixels = temp_table(f"temp_pixels_{uuid.uuid4().hex}", Pixel, ["x", "y"])

    canvas_t = Canvas.__table__
    pixel_t = Pixel.__table__

    sq = select([canvas_t.c.id]).where(canvas_t.c.nick == canvas).alias()
    q = select([pixel_t.c.x, pixel_t.c.y])
    q = q.where(and_(
        pixel_t.c.canvas_id.in_(sq),
        pixel_t.c.x.between(start_x, end_x),
        pixel_t.c.y.between(start_y, end_y),
        pixel_t.c.placed.between(start, end)))
    ins = insert(temp_pixels)
    ins = ins.from_select([temp_pixels.c.x, temp_pixels.c.y], q)

    with engine.connect() as con:
        con.execute(ins)

    x_values = []
    y_values = []
    j = 0

    cont = True
    while cont:
        xs = np.zeros(1000, dtype=np.int32)
        ys = np.zeros(1000, dtype=np.int32)

        sel = select([temp_pixels.c.x, temp_pixels.c.y])
        sel = sel.order_by(temp_pixels.c.x, temp_pixels.c.y)
        sel = sel.offset(1000 * j).limit(1000)  # 0,999 | 1000,1999 | 2000,2999 ...

        with engine.connect() as con:
            res = con.execute(sel)
            i = -1
            for i, (x, y) in enumerate(res):
                xs[i] = x
                ys[i] = y

        j += 1

        if i < 999:
            i += 1
            xs = xs[:-1000 + i]
            ys = ys[:-1000 + i]

            cont = False

        x_values.append(xs)
        y_values.append(ys)

    temp_pixels.drop(engine)

    x_values = np.concatenate(x_values)
    y_values = np.concatenate(y_values)

    if not len(x_values):
        raise NotEnoughDataError

    return x_values, y_values


def generate_cmap(color_map, overlay):
    steps = 256
    cmap = get_cmap(color_map, steps)

    if not overlay:
        return cmap

    # Copy colormap so we don't edit the global instance!
    cmap = copy.copy(cmap)

    # Override the alpha values of the cmap and regenerate it
    newcolors = cmap(np.linspace(0, 1, steps))
    # Alpha values for the bottom 1/4 of the cmap go from 0 to 1
    bottom_quarter = steps // 4
    alphas = np.linspace(0, 1, bottom_quarter)
    newcolors[:bottom_quarter, -1] = alphas
    newcmp = ListedColormap(newcolors)

    return newcmp


def hexbin_placement_density(ctx, x_values, y_values, color_map, bins, axes, center, overlay=None):
    fig = create_fig()
    ax = fig.subplots()

    bins = None if bins == "count" else bins

    cmap = generate_cmap(color_map, overlay)

    # gridsize is fixed because:
    # 1) the default plot warps somewhat without it
    # 2) we need to manually set linewidth to avoid hexagons overlapping so I
    # needed to manually adjust linewidth relative to a set gridsize so it looked good
    hb = ax.hexbin(x_values, y_values, gridsize=50, linewidths=0.1, bins=bins, cmap=cmap)
    cb = fig.colorbar(hb, ax=ax)
    if overlay:
        # Record and then reapply the limits the hexbin ideally wants to have, so it's hexagons
        # are even. This can warp the image, but eh, looks a lot better than the reverse.
        x, y = ax.get_xlim(), ax.get_ylim()
        # overlay = overlay.transpose(FLIP_TOP_BOTTOM)
        ax.imshow(overlay, extent=axes, origin="lower", alpha=0.25, aspect="auto")
        ax.set_xlim(x)
        ax.set_ylim(y)
    # Y coords are reversed on canvas sites
    ax.invert_yaxis()

    cb.set_label(ctx.s("canvas.hexbin_colorbar_count") if bins is None else ctx.s("canvas.hexbin_colorbar_log"))
    ax.set_xlabel(ctx.s("canvas.hexbin_xlabel"))
    ax.set_ylabel(ctx.s("canvas.hexbin_ylabel"))

    # Make sure the labels on the x axis don't run into one another
    x, y = center
    if x > 10000:
        locator = matplotlib.ticker.MaxNLocator(nbins=3)
        formatter = matplotlib.ticker.ScalarFormatter(useOffset=False)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

    return save_fig(fig)


def pixel_iter(pixels):
    for placed, x, y, color in pixels:
        yield [placed.timestamp(), x, y, color]


def online_iter(online_records):
    for time, count in online_records:
        yield [time.timestamp(), count]


def process_raw(ctx, canvas, start, end, type):
    if type == "placement":
        sq = ctx.session.query(Canvas.id).filter_by(nick=canvas)
        q = ctx.session.query(Pixel.placed, Pixel.x, Pixel.y, Pixel.color)
        q = q.filter(
            Pixel.placed.between(start, end),
            Pixel.canvas_id.in_(sq))
        pixels = q.order_by(Pixel.placed).all()

        if not len(pixels):
            raise NotEnoughDataError

        buf = StringIO()
        pixel_writer = csv.writer(buf)
        pixel_writer.writerow(["UTC Timestamp", "X", "Y", "ColorIndex"])
        pixel_writer.writerows(pixel_iter(pixels))
        buf.seek(0)
    elif type == "online":
        sq = ctx.session.query(Canvas.id).filter_by(nick=canvas)
        q = ctx.session.query(Online.time, Online.count)
        q = q.filter(
            Online.time.between(start, end),
            Online.canvas_id.in_(sq))
        online_records = q.order_by(Online.time).all()

        if not len(online_records):
            raise NotEnoughDataError

        buf = StringIO()
        online_writer = csv.writer(buf)
        online_writer.writerow(["UTC Timestamp", "Count"])
        online_writer.writerows(online_iter(online_records))
        buf.seek(0)

    return buf


def process_online_line(ctx, canvas, start, end):
    sq = ctx.session.query(Canvas.id).filter_by(nick=canvas)
    data = ctx.session.query(Online.time, Online.count).filter(
        Online.canvas_id.in_(sq),
        Online.time.between(start, end)).order_by(Online.time).all()

    if len(data) < 2:
        raise NotEnoughDataError

    x_values = np.zeros(len(data))
    y_values = np.zeros(len(data))

    for i, (time, count) in enumerate(data):
        x_values[i] = matplotlib.dates.date2num(time)
        y_values[i] = count

    return x_values, y_values


def online_line(ctx, x_values, y_values, duration, mean=False):
    fig = create_fig()
    ax = fig.subplots()

    ax.plot(x_values, y_values, linewidth=0.7)
    format_date(ctx, ax, duration)
    ax.set_ylabel(ctx.s("canvas.online_line_ylabel"))

    if mean:
        ax.plot(
            [duration.start, duration.end],
            [mean, mean],
            label=ctx.s("canvas.online_line_mean").format(mean),
            linewidth=0.7)
        fig.legend()

    ax.set_ylim(bottom=0)

    return save_fig(fig)


def histogram_2d_placement_density(ctx, x_values, y_values, color_map, axes, center, overlay=None):
    fig = create_fig()
    ax = fig.subplots()

    cmap = generate_cmap(color_map, overlay)

    bins = int(math.sqrt(len(x_values)))

    hist_data, x_edges, y_edges = np.histogram2d(x_values, y_values, bins=bins)
    hist_data = np.rot90(np.fliplr(hist_data))

    # left right bottom top
    extent = [x_edges[0], x_edges[-1], y_edges[-1], y_edges[0]]

    if overlay:
        ax.imshow(overlay, extent=axes, origin="upper", alpha=0.25)

    im = ax.imshow(hist_data, cmap=cmap, extent=extent, origin="upper", interpolation="gaussian")
    cb = fig.colorbar(im, ax=ax)

    ax.set_xlim(left=x_edges[0], right=x_edges[-1])
    ax.set_ylim(top=y_edges[0], bottom=y_edges[-1])

    cb.set_label(ctx.s("canvas.hexbin_colorbar_count"))
    ax.set_xlabel(ctx.s("canvas.hexbin_xlabel"))
    ax.set_ylabel(ctx.s("canvas.hexbin_ylabel"))

    # Make sure the labels on the x axis don't run into one another
    x, _ = center
    if x > 10000:
        locator = matplotlib.ticker.MaxNLocator(nbins=3)
        formatter = matplotlib.ticker.ScalarFormatter(useOffset=False)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(formatter)

    return save_fig(fig)


def process_placement_hist(ctx, canvas, duration):
    sq = ctx.session.query(Canvas.id).filter_by(nick=canvas)
    q = ctx.session.query(Pixel.placed).filter(
        Pixel.canvas_id.in_(sq),
        Pixel.placed.between(duration.start, duration.end))
    data = q.order_by(Pixel.placed).all()

    times = np.empty(len(data))
    for i, time in enumerate(data):
        times[i] = matplotlib.dates.date2num(time)

    return times


def placement_hist(ctx, times, duration, bins):
    fig = create_fig()
    ax = fig.subplots()

    log = True if bins == "log" else False

    start = matplotlib.dates.date2num(duration.start)
    end = matplotlib.dates.date2num(duration.end)

    ax.hist(times, bins="auto", range=[start, end], log=log, edgecolor="Black")
    format_date(ctx, ax, duration)

    ax.set_ylabel("Pixels placed log10(count)" if log else "Pixels placed")

    return save_fig(fig)
