import sqlite3

from objects.database_models import session_scope, Version, AnimoteUser, Guild, Template, MutedTemplate, Snapshot

if __name__ == "__main__":
    conn = sqlite3.connect('data/glimmer.db')
    c = conn.cursor()

    with session_scope() as session:
        # Initalise the version
        v = Version(version=3.0)
        session.add(v)

        # Migrate animote users
        c.execute("SELECT id FROM animote_users")
        animote_users = c.fetchall()
        session.add_all([AnimoteUser(id=u[0]) for u in animote_users])
        session.commit()

        # Migrate guilds
        c.execute("""SELECT id, name, join_date, prefix, alert_channel,
                     autoscan, canvas, language, template_admin, template_adder,
                     bot_admin, faction_name, faction_alias, faction_color, faction_desc,
                     faction_emblem, faction_invite
                     FROM guilds""")
        guilds = c.fetchall()

        for guild in guilds:
            session.add(Guild(
                id=guild[0],
                name=guild[1],
                join_date=guild[2],
                prefix=guild[3],
                alert_channel=guild[4],
                autoscan=guild[5],
                canvas=guild[6],
                language=guild[7],
                template_admin=guild[8],
                template_adder=guild[9],
                bot_admin=guild[10],
                faction_name=guild[11],
                faction_alias=guild[12],
                faction_color=guild[13],
                faction_desc=guild[14],
                faction_emblem=guild[15],
                faction_invite=guild[16]
            ))
        session.commit()

        # Migrate templates
        c.execute("""SELECT id, guild_id, name, url, canvas, x, y,
                     w, h, size, date_added, date_modified, md5, owner,
                     alert_id
                     FROM templates""")
        templates = c.fetchall()

        for template in templates:
            guilds = session.query(Guild).all()
            guild_ids = [g.id for g in guilds]

            if template[1] not in guild_ids:
                continue

            session.add(Template(
                id=template[0],
                guild_id=template[1],
                name=template[2],
                url=template[3],
                canvas=template[4],
                x=template[5],
                y=template[6],
                width=template[7],
                height=template[8],
                size=template[9],
                date_added=template[10],
                date_modified=template[11],
                md5=template[12],
                owner=template[13],
                alert_id=template[14]
            ))
        session.commit()

        # Migrate mutes
        c.execute("SELECT alert_id, template_id, expires FROM muted_templates")
        mutes = c.fetchall()
        session.add_all([MutedTemplate(
            alert_id=m[0], template_id=m[1], expires=m[2]
        ) for m in mutes])
        session.commit()

        # Migrate snapshots
        c.execute("SELECT base_template_id, target_template_id FROM snapshots")
        snapshots = c.fetchall()

        template_ids = [id[0] for id in session.query(Template.id).all()]
        snapshots = [s for s in snapshots if (s[0] in template_ids) and s[1] in template_ids]

        session.add_all([Snapshot(
            base_template_id=s[0],
            target_template_id=s[1]
        ) for s in snapshots])
        session.commit()
