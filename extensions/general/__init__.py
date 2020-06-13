from extensions.general.cogs import Cogs
from extensions.general.events import Events
from extensions.general.general import General


def setup(bot):
    bot.add_cog(Cogs(bot))
    bot.add_cog(Events(bot))
    bot.add_cog(General(bot))