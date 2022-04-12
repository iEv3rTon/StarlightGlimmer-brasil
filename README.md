<img align="right" width="200" height="200" src="avatar.jpg">

# Starlight Glimmer
A template utility bot.

This bot is forked from Fawfulcopter's original [Starlight Glimmer](https://github.com/DiamondIceNS/StarlightGlimmer).

Currently supports [Pixelcanvas.io](https://pixelcanvas.io), [Pixelzone.io](https://pixelzone.io) and [Pxls.space](https://pxls.space).

#### Requires:
- [Python](https://www.python.org/downloads/release/python-397/) v3.9
- [Postgresql](https://www.postgresql.org/)
- [Discord.py](https://github.com/Rapptz/discord.py/) v1.7.3
- [Pillow](https://pillow.readthedocs.io/en/latest/installation.html)
- [aiohttp](https://aiohttp.readthedocs.io/en/stable/) 
- [numpy](https://www.scipy.org/scipylib/download.html) 
- [websockets](https://pypi.org/project/websockets/)
- [hitherdither](https://www.github.com/hbldh/hitherdither)
- [fuzzywuzzy](https://github.com/seatgeek/fuzzywuzzy) 
- [python-Levenshtein](https://github.com/ztane/python-Levenshtein/) 

#### Installation:
1. Install Python 3.6
2. Install Postgresql
3. Run `python -m pip install -r requirements.txt` in the main directory
4. Put your bot token, [database uri](https://docs.sqlalchemy.org/en/13/core/engines.html#database-urls) and other config info in `config/config.json.example`
5. Rename `config.json.example` to `config.json`
6. Make a folder named data
7. Enable all three [privileged intents](https://discordpy.readthedocs.io/en/master/intents.html) in the discord developer portal for your bot user
8. Run `python glimmer.py`

#### Features:
- Automatic live canvas preview
- Automatic live template checking
- Template storage for easy access to templates you care about most
- Faction creation, to share your templates with other guilds
- Color quantization of templates to canvas palette
- Dithering of images to canvas palettes using a variety of algorithms
- Gridifyer to create gridded, human-readable templates
- Dithering sample charts for assisting color selection when you are making a template
- Configurable roles
- [Animotes](https://github.com/ev1l0rd/animotes) support, just because
- Full language localization
- Live alerts for damage on templates

For a more in-depth walkthrough of Glimmer's core functions, see [the wiki page](https://github.com/BrickGrass/StarlightGlimmer/wiki).

#### Languages:
- English (US)
- Portuguese (BR) - Special thanks to Ataribr / ✠ /#6703 and Brian Deneke#9654
- Turkish (TR) (partial) - Special thanks to furkan#3251

If you happen to know a language that is not listed and would be willing to translate, please translate the strings in `lang/en_US.py` and submit a pull request.

#### Help:
If you need assistance with the bot, have a problem, or would like to recommend a feature to me directly, you can contact me on discord, I am BrickGrass#8455.

[avatar]: avatar.jpg
