import asyncio
import logging

from utils import canvases

log = logging.getLogger(__name__)


class Tracker:
    def __init__(self, bot, ctx, canvas, pixels, embed, index):
        self.bot = bot
        self.ctx = ctx
        self.canvas = canvas
        self.pixels = pixels
        self.embed = embed
        self.index = index

        self.sending = False
        self.msg = None

    async def send_err_embed(self):
        if self.sending:
            return
        self.sending = True

        out = []
        for p in self.pixels:
            if p.current != p.target:
                current = self.ctx.s("color.{}.{}".format(self.canvas, p.current))
                target = self.ctx.s("color.{}.{}".format(self.canvas, p.target))
                url = canvases.url_templates[self.canvas].format(p.x, p.y)
                out.append(self.ctx.s("bot.pixel").format(x=p.x, y=p.y, url=url, current=current, target=target))
                if len(out) == 10:
                    out.append("...")
                    break
        if out == []:
            out = self.ctx.s("canvas.diff_fixed")
        else:
            out = "\n".join(out)

        try:
            current = self.embed.fields[self.index]
            self.embed.set_field_at(
                self.index,
                name=self.ctx.s("canvas.diff_error_title"),
                value=out,
                inline=False
            )
        except IndexError:
            self.embed.add_field(
                name=self.ctx.s("canvas.diff_error_title"),
                value=out,
                inline=False
            )

        await self.msg.edit(embed=self.embed)
        # Release send lock
        self.sending = False

    async def on_message(self, x, y, color, canvas):
        if self.canvas != canvas:
            return

        for p in self.pixels:
            if p.x == x and p.y == y:
                p.current = color
                self.bot.loop.create_task(self.send_err_embed())

    async def connect(self, msg):
        self.msg = msg
        await self.send_err_embed()

        subscribe = self.bot.subscribers[self.canvas]
        unsubscribe = self.bot.unsubscribers[self.canvas]

        l_uuid = await subscribe(self.on_message)
        await asyncio.sleep(60 * 5)
        await unsubscribe(l_uuid)

        self.embed.set_footer(text=self.ctx.s("canvas.diff_timeout"))
        await self.msg.edit(embed=self.embed)
