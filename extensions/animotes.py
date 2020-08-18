import re

import discord
from discord.ext import commands

from objects.database_models import AnimoteUser, session_scope


#    Cog to reformat messages to allow for animated emotes, regardless of nitro status
#    and sharing those emotes with other servers with opt-in policy.
#    Copyright (C) 2017-2018 Valentijn <ev1l0rd> and DiamondIceNS
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.

#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.


class Animotes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def register(self, ctx):
        user = AnimoteUser(id=ctx.author.id)
        ctx.session.add(user)
        await ctx.send(ctx.s("animotes.opt_in"))

    @commands.command()
    async def unregister(self, ctx):
        user = ctx.session.query(AnimoteUser).get(ctx.author.id)
        if user:
            ctx.session.delete(user)
        await ctx.send(ctx.s("animotes.opt_out"))

    @commands.Cog.listener()
    async def on_message(self, message):
        with session_scope() as session:
            if not message.author.bot and session.query(AnimoteUser).get(message.author.id):
                channel = message.channel
                content = emote_corrector(message)
                if content:
                    await message.delete()
                    await channel.send(content=content)


# noinspection PyTypeChecker
def emote_corrector(message):
    """Recognises if a message needs to be replaced with an animote message and returns the text for that message if so.

    Arguments:
    message - A discord.Message object.

    Returns:
    A fully formatted string containing the emoji requested.
    """
    r = re.compile(r'(?<![a<]):[\w~]+:')
    found = r.findall(message.content)
    emotes = []
    for em in found:
        temp = discord.utils.get(message.guild.emojis, name=em[1:-1])
        try:
            if temp.animated:
                emotes.append((em, str(temp)))
        except AttributeError:
            pass  # We only care about catching this, not doing anything with it

    if emotes:
        temp = message.content
        for em in set(emotes):
            temp = temp.replace(*em)
    else:
        return None

    escape = re.compile(r':*<\w?:\w+:\w+>')
    # This escapes all colons that come before an emoji;
    # thanks to Discord shenanigans, this is needed.
    for esc in set(escape.findall(temp)):
        temp_esc = esc.split('<')
        esc_s = '{}<{}'.format(temp_esc[0].replace(':', '\:'), temp_esc[1])
        temp = temp.replace(esc, esc_s)

    temp = '**<{}>** '.format(message.author.name) + temp

    return temp


def setup(bot):
    bot.add_cog(Animotes(bot))
