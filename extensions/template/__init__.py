from extensions.template.template import Template
from extensions.template.alerts import Alerts


def setup(bot):
    bot.add_cog(Template(bot))
    bot.add_cog(Alerts(bot))
