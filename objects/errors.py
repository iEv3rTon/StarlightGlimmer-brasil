from discord.ext import commands
from fuzzywuzzy import fuzz
from utils import sqlite as sql

class IgnoreError(commands.CommandError):
    pass


class BadArgumentErrorWithMessage(commands.CommandError):
    def __init__(self, message):
        self.message = message


class FactionNotFoundError(commands.CommandError):
    pass


class HttpGeneralError(commands.CommandError):
    pass


class HttpCanvasError(commands.CommandError):
    def __init__(self, canvas):
        self.canvas = canvas


class IdempotentActionError(commands.CommandError):
    pass


class NoAttachmentError(commands.CommandError):
    pass


class NoSelfPermissionError(commands.CommandError):
    pass


class NoTemplatesError(commands.CommandError):
    def __init__(self, is_canvas_specific=False):
        self.is_canvas_specific = is_canvas_specific


class NoUserPermissionError(commands.CommandError):
    pass


class NoJpegsError(commands.CommandError):
    pass


class NotPngError(commands.CommandError):
    pass


class PilImageError(commands.CommandError):
    pass


class TemplateHttpError(commands.CommandError):
    def __init__(self, template_name):
        self.template_name = template_name


class TemplateNotFoundError(commands.CommandError):
    def __init__(self, gid, template_name):
        templates = sql.template_get_all_by_guild_id(gid)
        matches = []
        for t in templates:
            ratio = fuzz.partial_ratio(t.name, template_name)
            if ratio >= 70:
                matches.append([t, ratio])
        matches.sort(key=lambda match: match[1])
        self.matches = [f"`{t[0].name}`" for i, t in enumerate(matches) if i < 5]
        self.query = template_name


class UrlError(commands.CommandError):
    pass


class ColorError(commands.CommandError):
    pass
