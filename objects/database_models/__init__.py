from contextlib import contextmanager
import sys
import logging

from sqlalchemy import create_engine, Table
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

from objects.database_models.animote_user import AnimoteUser
from objects.database_models.guild import Guild
from objects.database_models.menu_lock import MenuLock
from objects.database_models.muted_template import MutedTemplate
from objects.database_models.snapshot import Snapshot
from objects.database_models.template import Template
from objects.database_models.version import Version
from objects.database_models.pixel import Pixel
from objects.database_models.canvas import Canvas
from objects.database_models.online import Online

from utils import config, version as v

log = logging.getLogger(__name__)

engine = create_engine(config.DATABASE_URI, connect_args={"options": "-c timezone=utc"})
session_factory = sessionmaker(bind=engine, expire_on_commit=False)
Session = scoped_session(session_factory)

Base.metadata.create_all(engine)

# Id autoincrement behaviour breaks if I don't do this apparently, hhh
if engine.name == "postgresql":
    with engine.connect() as con:
        con.execute("SELECT setval('templates_id_seq', max(id)) FROM templates;")
        con.execute("SELECT setval('snapshots_id_seq', max(id)) FROM snapshots;")
        con.execute("SELECT setval('canvases_id_seq', max(id)) FROM canvases;")
        con.execute("SELECT setval('pixels_id_seq', max(id)) FROM pixels;")
        con.execute("SELECT setval('online_records_id_seq', max(id)) FROM online_records;")


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    finally:
        session.close()


def upgrade(version):
    if not version:
        return

    if engine.name != "postgresql":
        log.critical("Unsupported backend for db migrations, look at objects/database_models/__init__.py and execute equivalent sql for your database.")
        print("Unsupported backend for db migrations, look at objects/database_models/__init__.py and execute equivalent sql for your database.")
        sys.exit(1)

    if version > 3.0:
        with engine.connect() as con:
            con.execute("ALTER TABLE templates ADD alert_stats jsonb;")
    elif version > 3.1:
        with engine.connect() as con:
            con.execute("ALTER TABLE templates DROP COLUMN alert_stats;")


with session_scope() as session:
    version = session.query(Version).get(1)
    if not version:
        version = Version(version=v.VERSION)
        session.add(version)
        session.commit()

    if version.version != v.VERSION:
        upgrade(v.VERSION)
        session.query(Version).filter_by(id=1).update({"version": v.VERSION})
        session.commit()


def temp_table(name, base_table, cols):
    columns = [c.copy() for c in base_table.__table__.c if c.name in cols]
    table = Table(name, Base.metadata, *columns, extend_existing=True)
    table.create(engine)
    return table
