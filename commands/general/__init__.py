from commands.general.cogs import Cogs
from commands.general.errors import Errors
from commands.general.general import General


def setup(bot):
    bot.add_cog(Cogs(bot))
    bot.add_cog(Errors(bot))
    bot.add_cog(General(bot))