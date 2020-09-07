import logging

from discord import TextChannel, Role
from discord.ext import commands
from discord.utils import get as dget

import utils
from utils import checks
from objects.database_models import Guild

log = logging.getLogger(__name__)


class Configuration(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @checks.admin_only()
    @commands.guild_only()
    @commands.group(name="alertchannel", invoke_without_command=True, case_insensitive=True)
    async def alertchannel(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        channel = dget(ctx.guild.channels, id=guild.alert_channel)
        if channel:
            await ctx.send(ctx.s("configuration.alert_channel_current").format(channel.mention))
        else:
            await ctx.send(ctx.s("configuration.alert_channel_none"))

    @checks.admin_only()
    @commands.guild_only()
    @alertchannel.command(name="set")
    async def alertchannel_set(self, ctx, channel: TextChannel):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.alert_channel = channel.id
        await ctx.send(ctx.s("configuration.alert_channel_set").format(channel.mention))

    @checks.admin_only()
    @commands.guild_only()
    @alertchannel.command(name="clear")
    async def alertchannel_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.alert_channel = None
        await ctx.send(ctx.s("configuration.alert_channel_cleared"))

    @checks.admin_only()
    @commands.guild_only()
    @commands.command()
    async def prefix(self, ctx, *prefix):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if prefix:
            if len(prefix[0]) > 5:
                raise commands.BadArgument
            guild.prefix = prefix
            sql.guild_update(ctx.guild.id, prefix=prefix)
            await ctx.send(ctx.s("configuration.prefix_set").format(prefix))
        else:
            await ctx.send(ctx.s("configuration.prefix_current").format(guild.prefix if guild.prefix else utils.config.PREFIX))

    @checks.admin_only()
    @commands.guild_only()
    @commands.command()
    async def autoscan(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if guild.autoscan is False:
            guild.autoscan = True
            await ctx.send(ctx.s("configuration.autoscan_enabled"))
        else:
            guild.autoscan = False
            await ctx.send(ctx.s("configuration.autoscan_disabled"))

    @checks.admin_only()
    @commands.guild_only()
    @commands.group(name="canvas", invoke_without_command=True, case_insensitive=True)
    async def canvas(self, ctx):
        out = [ctx.s("configuration.canvas_check_1").format(ctx.canvas_pretty),
               ctx.s("configuration.canvas_check_2").format(ctx.prefix)]
        await ctx.send('\n'.join(out))

    @checks.admin_only()
    @commands.guild_only()
    @canvas.command(name="pixelcanvas", aliases=["pc"])
    async def canvas_pixelcanvas(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.canvas = "pixelcanvas"
        await ctx.send(ctx.s("configuration.canvas_set").format("Pixelcanvas.io"))

    @checks.admin_only()
    @commands.guild_only()
    @canvas.command(name="pixelzone", aliases=["pz"])
    async def canvas_pixelzone(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.canvas = "pixelzone"
        await ctx.send(ctx.s("configuration.canvas_set").format("Pixelzone.io"))

    @checks.admin_only()
    @commands.guild_only()
    @canvas.command(name="pxlsspace", aliases=["ps"])
    async def canvas_pxlsspace(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.canvas = "pxlsspace"
        await ctx.send(ctx.s("configuration.canvas_set").format("Pxls.space"))

    @checks.admin_only()
    @commands.guild_only()
    @commands.command()
    async def language(self, ctx, option=None):
        if not option:
            out = [
                ctx.s("configuration.language_check_1").format(ctx.langs[ctx.lang]),
                ctx.s("configuration.language_check_2"),
                "```"
            ]
            for code, name in ctx.langs.items():
                out.append("{0} - {1}".format(code, name))
            out.append("```")
            await ctx.send('\n'.join(out))
            return
        if option.lower() not in ctx.langs:
            return
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.language = option.lower()
        ctx.session.commit()
        self.bot.get_guild_language.cache_clear()
        await ctx.send(ctx.s("configuration.language_set").format(ctx.langs[option.lower()]))

    @checks.admin_only()
    @commands.guild_only()
    @commands.group(name="role", invoke_without_command=True, case_insensitive=True)
    async def role(self, ctx):
        roles = ["botadmin", "templateadder", "templateadmin"]
        out = ["**{}**".format(ctx.s("configuration.role_list_header")), "```xl"]
        max_len = max(map(lambda x: len(x), roles))
        for r in roles:
            out.append("{0:<{max_len}} - {1}".format(r, ctx.s("configuration.role_list_" + r), max_len=max_len))
        out.append("```")
        await ctx.send('\n'.join(out))

    @checks.admin_only()
    @commands.guild_only()
    @role.group(name="botadmin", invoke_without_command=True, case_insensitive=True)
    async def role_botadmin(self, ctx):
        r = utils.get_botadmin_role(ctx)
        if r:
            await ctx.send(ctx.s("configuration.role_bot_admin_check").format(r.name))
        else:
            await ctx.send(ctx.s("configuration.role_bot_admin_not_set"))

    @checks.admin_only()
    @commands.guild_only()
    @role_botadmin.command(name="set")
    async def role_botadmin_set(self, ctx, *, role: Role):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.bot_admin = role.id
        await ctx.send(ctx.s("configuration.role_bot_admin_set").format(role.name))

    @checks.admin_only()
    @commands.guild_only()
    @role_botadmin.command(name="clear")
    async def role_botadmin_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.bot_admin = None
        await ctx.send(ctx.s("configuration.role_bot_admin_cleared"))

    @checks.admin_only()
    @commands.guild_only()
    @role.group(name="templateadder", invoke_without_command=True, case_insensitive=True)
    async def role_templateadder(self, ctx):
        r = utils.get_templateadder_role(ctx)
        if r:
            await ctx.send(ctx.s("configuration.role_template_adder_check").format(r.name))
        else:
            await ctx.send(ctx.s("configuration.role_template_adder_not_set"))

    @checks.admin_only()
    @commands.guild_only()
    @role_templateadder.command(name="set")
    async def role_templateadder_set(self, ctx, *, role: Role):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.template_adder = role.id
        await ctx.send(ctx.s("configuration.role_template_adder_set").format(role.name))

    @checks.admin_only()
    @commands.guild_only()
    @role_templateadder.command(name="clear")
    async def role_templateadder_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.template_adder = None
        await ctx.send(ctx.s("configuration.role_template_adder_cleared"))

    @checks.admin_only()
    @commands.guild_only()
    @role.group(name="templateadmin", invoke_without_command=True, case_insensitive=True)
    async def role_templateadmin(self, ctx):
        r = utils.get_templateadmin_role(ctx)
        if r:
            await ctx.send(ctx.s("configuration.role_template_admin_check").format(r.name))
        else:
            await ctx.send(ctx.s("configuration.role_template_admin_not_set"))

    @checks.admin_only()
    @commands.guild_only()
    @role_templateadmin.command(name="set")
    async def role_templateadmin_set(self, ctx, *, role: Role):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.template_admin = role.id
        await ctx.send(ctx.s("configuration.role_template_admin_set").format(role.name))

    @checks.admin_only()
    @commands.guild_only()
    @role_templateadmin.command(name="clear")
    async def role_templateadmin_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        guild.template_admin = None
        await ctx.send(ctx.s("configuration.role_template_admin_cleared"))


def setup(bot):
    bot.add_cog(Configuration(bot))
