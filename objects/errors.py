from discord.ext import commands
from fuzzywuzzy import fuzz
from utils.database import session_scope, Guild


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
        with session_scope() as session:
            templates = session.query(Guild).get(gid).templates

            matches = []
            for t in templates:
                ratio = fuzz.partial_ratio(t.name, template_name)
                if ratio >= 70:
                    t.ratio = ratio
                    matches.append(t)
            matches.sort(key=lambda match: match.ratio)
            self.matches = [f"`{t.name}`" for t in matches[0:5]]
            self.query = template_name


class UrlError(commands.CommandError):
    pass


class ColorError(commands.CommandError):
    pass


class TemplateTooLargeError(commands.CommandError):
    def __init__(self, limit):
        self.limit = limit


class CanvasNotSupportedError(commands.CommandError):
    pass


class GuildNotFoundError(commands.CommandError):
    pass
