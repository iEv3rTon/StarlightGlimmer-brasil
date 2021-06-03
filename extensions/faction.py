import logging
import io
import re

import discord
from discord.ext import commands
from PIL import Image

from objects.errors import BadArgumentErrorWithMessage, NoSelfPermissionError, UrlError
from utils import canvases, checks
from objects.database_models import Guild, Template

log = logging.getLogger(__name__)


class Faction(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @checks.admin_only()
    @commands.command(name="assemble")
    async def assemble(self, ctx, name, alias=""):
        guild = ctx.session.query(Guild).get(ctx.guild.id)

        if guild.is_faction:
            await ctx.send(ctx.s("faction.already_faction"))
            return
        name = re.sub(r"[^\S ]+", "", name)
        if not (6 <= len(name) <= 32):
            raise BadArgumentErrorWithMessage(ctx.s("faction.err.name_length"))
        if ctx.session.query(Guild).filter_by(faction_name=name).first():
            await ctx.send(ctx.s("faction.name_already_exists"))
            return
        if alias:
            alias = re.sub(r"[^A-Za-z]+", "", alias).lower()
            if alias and not (1 <= len(alias) <= 5):
                raise BadArgumentErrorWithMessage(ctx.s("faction.err.alias_length"))
            if ctx.session.query(Guild).filter_by(faction_alias=alias).first():
                await ctx.send(ctx.s("faction.alias_already_exists"))
                return

        guild.faction_name = name
        guild.faction_alias = alias
        await ctx.send(ctx.s("faction.assembled").format(name))

    @checks.admin_only()
    @commands.command(name="disband")
    async def disband(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)

        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))

        guild.faction_name = None
        guild.faction_alias = None
        guild.faction_emblem = None
        guild.faction_invite = None
        guild.faction_hidden = False

        await ctx.send(ctx.s("faction.disbanded"))

    @checks.admin_only()
    @commands.group(name="faction", case_insensitive=True)
    async def faction(self, ctx):
        pass

    @faction.group(name="alias", case_insensitive=True)
    async def faction_alias(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)

        if not guild.is_faction:
            await ctx.send(ctx.s("faction.must_be_a_faction"))
            return
        if not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "alias":
            alias = guild.faction_alias
            if alias:
                await ctx.send(alias)
            else:
                await ctx.send(ctx.s("faction.no_alias"))

    @faction_alias.command(name="clear")
    async def faction_alias_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            await ctx.send(ctx.s("faction.must_be_a_faction"))
            return
        guild.faction_alias = None
        await ctx.send(ctx.s("faction.clear_alias"))

    @faction_alias.command(name="set")
    async def faction_alias_set(self, ctx, new_alias):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        new_alias = re.sub("[^A-Za-z]+", "", new_alias).lower()
        if not (1 <= len(new_alias) <= 5):
            raise BadArgumentErrorWithMessage(ctx.s("faction.err.alias_length"))
        if ctx.session.query(Guild).filter_by(faction_alias=new_alias).first():
            return await ctx.send(ctx.s("faction.alias_already_exists"))
        guild.faction_alias = new_alias
        await ctx.send(ctx.s("faction.set_alias").format(new_alias))

    @faction.group(name="color", aliases=["colour"], case_insensitive=True)
    async def faction_color(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        if not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "color":
            img = Image.new('RGB', (32, 32), guild.faction_color)
            with io.BytesIO() as bio:
                img.save(bio, format="PNG")
                bio.seek(0)
                f = discord.File(bio, "color.png")
                await ctx.send('0x{0:06X}'.format(guild.faction_color), file=f)

    @faction_color.command(name="clear")
    async def faction_color_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_color = None
        await ctx.send(ctx.s("faction.clear_color"))

    @faction_color.command(name="set")
    async def faction_color_set(self, ctx, color: str):
        try:
            color = abs(int(color, 16) % 0xFFFFFF)
        except ValueError:
            return await ctx.send(ctx.s("error.invalid_color"))
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_color = color
        await ctx.send(ctx.s("faction.set_color"))

    @faction.group(name="desc", case_insensitive=True)
    async def faction_desc(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        if not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "desc":
            if guild.faction_desc:
                await ctx.send(guild.faction_desc)
            else:
                await ctx.send(ctx.s("faction.no_description"))

    @faction_desc.command(name="clear")
    async def faction_desc_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_desc = None
        await ctx.send(ctx.s("faction.clear_description"))

    @faction_desc.command(name="set")
    async def faction_desc_set(self, ctx, *, description):
        description = re.sub(r"[^\S ]+", "", description)
        if not (len(description) <= 240):
            raise BadArgumentErrorWithMessage(ctx.s("faction.err.description_length"))
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_desc = description
        await ctx.send(ctx.s("faction.set_description"))

    @faction.group(name="emblem", case_insensitive=True)
    async def faction_emblem(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        if not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "emblem":
            if guild.faction_emblem:
                await ctx.send(guild.faction_emblem)
            else:
                await ctx.send(ctx.s("faction.no_emblem"))

    @faction_emblem.command(name="clear")
    async def faction_emblem_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_emblem = None
        await ctx.send(ctx.s("faction.clear_emblem"))

    @faction_emblem.command(name="set")
    async def faction_emblem_set(self, ctx, emblem_url=None):
        if emblem_url:
            if not re.search(r"^(?:https?://)cdn\.discordapp\.com/", emblem_url):
                raise UrlError
        elif len(ctx.message.attachments) > 0:
            emblem_url = ctx.message.attachments[0].url

        if not emblem_url:
            return

        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))

        guild.faction_emblem = emblem_url
        await ctx.send(ctx.s("faction.set_emblem"))

    @faction.group(name="invite", case_insensitive=True)
    async def faction_invite(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        if not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "invite":
            if guild.faction_invite:
                await ctx.send(guild.faction_invite)
            else:
                await ctx.send(ctx.s("faction.no_invite"))

    @faction_invite.command(name="clear")
    async def faction_invite_clear(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_invite = None
        await ctx.send(ctx.s("faction.clear_invite"))

    @faction_invite.command(name="set")
    async def faction_invite_set(self, ctx, url=None):
        if url:
            try:
                invite = await self.bot.get_invite(url)
            except discord.NotFound:
                return await ctx.send(ctx.s("faction.err.invalid_invite"))
            if invite.guild.id != ctx.guild.id:
                return await ctx.send(ctx.s("faction.err.invite_not_this_guild"))
            if not re.match(r"(?:https?://)discord\.gg/\w+", url):
                url = "https://discord.gg/" + url
        else:
            if not ctx.channel.permissions_for(ctx.guild.me).create_instant_invite:
                raise NoSelfPermissionError
            invite = await ctx.channel.create_invite(reason="Invite for faction info page")
            url = invite.url

        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        guild.faction_invite = url
        await ctx.send(ctx.s("faction.set_invite"))

    @faction.group(name="name", case_insensitive=True)
    async def faction_name(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            return await ctx.send(ctx.s("faction.must_be_a_faction"))
        elif not ctx.invoked_subcommand or ctx.invoked_subcommand.name == "name":
            await ctx.send(guild.faction_name)

    @faction_name.command(name="set")
    async def faction_name_set(self, ctx, new_name):
        new_name = re.sub(r"[^\S ]+", "", new_name)
        if not (6 <= len(new_name) <= 32):
            raise BadArgumentErrorWithMessage(ctx.s("faction.err.name_length"))
        elif ctx.session.query(Guild).filter_by(faction_name=new_name).first():
            await ctx.send(ctx.s("faction.name_already_exists"))
        else:
            guild = ctx.session.query(Guild).get(ctx.guild.id)
            if not guild.is_faction:
                return await ctx.send(ctx.s("faction.must_be_a_faction"))
            guild.faction_name = new_name
            await ctx.send(ctx.s("faction.set_name").format(new_name))

    @commands.command(name="factionlist", aliases=['fl'])
    async def factionlist(self, ctx, page: int = 1):
        fs = ctx.session.query(Guild).filter(
            Guild.faction_name != None,
            Guild.faction_hidden == False).order_by(
                Guild.faction_name).all()

        if len(fs) > 0:
            pages = 1 + len(fs) // 10
            page = min(max(page, 1), pages)

            msg = [
                "**{}** - {} {}/{}".format(ctx.s("faction.list_header"), ctx.s("bot.page"), page, pages),
                "```xl",
                "{0:<34}  {1:<5}".format(ctx.s("bot.name"), ctx.s("bot.alias"))
            ]
            for f in fs[(page - 1) * 10:page * 10]:
                alias = '"{}"'.format(f.faction_alias) if f.faction_alias else ""
                msg.append("{0:<34}  {1:<5}".format('"{}"'.format(f.faction_name), alias))
            msg.append("")
            msg.append("// " + ctx.s("faction.faction_list_footer_1").format(ctx.prefix))
            msg.append("// " + ctx.s("faction.faction_list_footer_2").format(ctx.prefix))
            msg.append("```")
            await ctx.send('\n'.join(msg))
        else:
            await ctx.send(ctx.s("faction.no_factions"))

    @checks.admin_only()
    @commands.command(name="hide")
    async def hide(self, ctx):
        guild = ctx.session.query(Guild).get(ctx.guild.id)
        if not guild.is_faction:
            await ctx.send(ctx.s("faction.not_a_faction_yet"))
            return

        if guild.faction_hidden is True:
            guild.faction_hidden = False
            await ctx.send(ctx.s("faction.clear_hide").format(guild.faction_name))
        else:
            guild.faction_hidden = True
            await ctx.send(ctx.s("faction.set_hide").format(guild.faction_name))

    @commands.command(name="factioninfo", aliases=['fi'])
    async def factioninfo(self, ctx, other=None):
        if other:
            guild = ctx.session.query(Guild).filter_by(faction_name=other).first()
            if not guild:
                guild = ctx.session.query(Guild).filter_by(faction_alias=other.lower()).first()
        else:
            guild = ctx.session.query(Guild).get(ctx.guild.id)

        if not guild:
            await ctx.send(ctx.s("error.faction_not_found"))
            return
        if not guild.is_faction:
            await ctx.send(ctx.s("faction.not_a_faction_yet"))
            return

        templates = guild.templates.all()
        canvas_list = set([t.canvas for t in templates])

        canvases_pretty = []
        for c in canvas_list:
            canvases_pretty.append(canvases.pretty_print[c])
        canvases_pretty.sort()
        canvas_list = '\n'.join(canvases_pretty)

        e = discord.Embed(color=guild.faction_color)
        if canvas_list:
            e.add_field(name=ctx.s("bot.canvases"), value='\n'.join(canvases_pretty))
        if guild.faction_invite:
            icon_url = self.bot.Guild.get(guild.id).icon_url
            e.set_author(name=guild.faction_name, url=guild.faction_invite, icon_url=icon_url)
        else:
            e.set_author(name=guild.faction_name)
        e.description = guild.faction_desc if guild.faction_desc else ""
        if guild.faction_alias:
            e.description = "**{}:** {}\n".format(ctx.s("bot.alias"), guild.faction_alias) + e.description
        if guild.faction_emblem:
            e.set_thumbnail(url=guild.faction_emblem)

        await ctx.send(embed=e)


def setup(bot):
    bot.add_cog(Faction(bot))
