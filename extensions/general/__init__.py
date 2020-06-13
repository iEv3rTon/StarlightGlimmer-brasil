from extensions.general.cogs import Cogs
from extensions.general.errors import Errors
from extensions.general.general import General


def setup(bot):
    bot.add_cog(Cogs(bot))
    bot.add_cog(Errors(bot))
    bot.add_cog(General(bot))