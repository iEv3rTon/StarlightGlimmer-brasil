<img align="right" width="200" height="200" src="avatar.jpg">

# Starlight Glimmer
A template utility bot based on [Alastair](Make-Alastair-Great-Again) and [Pinkie Pie](https://pastebin.com/Tg1p5AnW).

This bot is forked from Fawfulcopter's original [Starlight Glimmer](https://github.com/DiamondIceNS/StarlightGlimmer).

Currently supports [Pixelcanvas.io](https://pixelcanvas.io/). Features for other pixel sites are still somewhat functional, but I will not be maintaining them.

**Invite:** `https://discordapp.com/oauth2/authorize?client_id=589606792926068736&permissions=35840&scope=bot`

#### Requires:
- [Python](https://www.python.org/downloads/release/python-365/) v3.6
- [Discord.py](https://github.com/Rapptz/discord.py/) v1.3.1
- [Pillow](https://pillow.readthedocs.io/en/latest/installation.html) v5.1.0
- [aiohttp](https://aiohttp.readthedocs.io/en/stable/) v3.5.4
- [numpy](https://www.scipy.org/scipylib/download.html) v1.14.4
- [websockets](https://pypi.org/project/websockets/) v4.0.1
- [hitherdither](https://www.github.com/hbldh/hitherdither)
- [fuzzywuzzy](https://github.com/seatgeek/fuzzywuzzy) v0.18.0
- [python-Levenshtein](https://github.com/ztane/python-Levenshtein/) v0.12.0

#### Installation:
1. Install Python 3.6
2. Run `pip install -r requirements.txt` in the main directory
3. Put your bot token and other config info in `config/config.json.example`
3. Rename `config.json.example` to `config.json`
4. Make a folder named data
4. Run `python glimmer.py`

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

For a more in-depth walkthrough of Glimmer's core functions, see [the wiki page](https://github.com/BrickGrass/StarlightGlimmer/wiki).

#### Languages:
- English (US)
- Portuguese (BR) - Special thanks to Ataribr / ✠ /#6703 and Brian Deneke#9654
- Turkish (TR) (partial) - Special thanks to furkan#3251

If you happen to know a language that is not listed and would be willing to translate, please translate the strings in `lang/en_US.py` and submit a pull request.

#### Help:
If you need assistance with the bot, have a problem, or would like to recommend a feature to me directly, you can contact me on discord, I am BrickGrass#8455.

[avatar]: avatar.jpg
