Configuration Cog
=================

Alertchannel
------------
``g!alertchannel (subcommand)``

Autoscan
--------
``g!autoscan``

Toggles automatic :ref:`previews <Preview>` and :ref:`diffs <Diff>`.

If enabled, I will watch all messages for coordinates and automatically create previews and diffs according to these rules:

* Any message with coordinates in the form "@0, 0" will trigger a preview for the default canvas :ref:`(see g!canvas) <Canvas>`
* Any message with a link to a supported canvas will trigger a preview for that canvas.
* Any message with coordinates in the form "0, 0" with a PNG attached will trigger a diff for the default canvas.
* Previews take precedence over diffs
* Messages which use spoiler tags will be entirely ignored

Canvas
------
``g!canvas (subcommand)``

View and set the default canvas website for a guild, this defaults to Pixelcanvas.io.

Language
--------
``g!language (code)``

View and set the language the bot will use for a guild, this defaults to en_US.

Prefix
------
``g!prefix (prefix)``

View and set the command prefix for a guild. Max length is 5 characters. You really shouldn't need more than 2.

Role
----
``g!role (subcommand)``

View and configure the permissions that various roles are assigned on a guild.

The permission levels available are:

* botadmin - Can do anything an Administrator can do
* templateadder - Can add templates, and remove templates they added themself
* templateadmin - Can add and remove any template

By default those with Administrator permissions will always have ``botadmin`` permissions and everyone else will have ``templateadder`` permissions.
To customise this (eg: giving your moderators more permissions or not letting those without a role remove any templates, even those they added themselves) you need to use the subcommands.

Role Botadmin
^^^^^^^^^^^^^
``g!role botadmin`` or ``g!role botadmin clear`` or ``g!role botadmin set <role>``

View, clear or set the current role that has ``botadmin`` permissions.

Role Templateadder
^^^^^^^^^^^^^^^^^^
``g!role templateadder`` or ``g!role templateadder clear`` or ``g!role templateadder set <role>``

View, clear or set the current role that has ``templateadder`` permissions.

Role Templateadmin
^^^^^^^^^^^^^^^^^^
``g!role templateadmin`` or ``g!role templateadmin clear`` or ``g!role templateadmin set <role>``

View, clear or set the current role that has ``templateadmin`` permissions.
