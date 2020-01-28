from discord.ext import commands

from objects.errors import NoUserPermissionError
import utils


def admin_only():
    """Check decorator that requires admin perms to pass"""
    def predicate(ctx):
        if not ctx.guild:
            return True
        if utils.is_admin(ctx):
            return True
        else:
            raise NoUserPermissionError
    return commands.check(predicate)


def template_admin_only():
    """Check decorator that requires template admin perms to pass"""
    def predicate(ctx):
        if not ctx.guild:
            return True
        if utils.is_template_admin(ctx) or utils.is_admin(ctx):
            return True
        else:
            raise NoUserPermissionError
    return commands.check(predicate)


def template_adder_only():
    """Check decorator that requires the template adder to pass"""
    def predicate(ctx):
        if not ctx.guild:
            return True
        if utils.is_template_adder(ctx) or utils.is_template_admin(ctx) or utils.is_admin(ctx):
            return True
        else:
            raise NoUserPermissionError
    return commands.check(predicate)
