from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

from utils import config
from objects.errors import GuildNotFoundError

Base = declarative_base()

from utils.database.animote_user import AnimoteUser
from utils.database.guild import Guild
from utils.database.menu_lock import MenuLock
from utils.database.muted_template import MutedTemplate
from utils.database.snapshot import Snapshot
from utils.database.template import Template
from utils.database.version import Version

# whenever stuff is accessed from a thread we will need to call
# engine.dispose()
# to make sure we don't get errors. see:
# https://docs.sqlalchemy.org/en/13/core/pooling.html#using-connection-pools-with-multiprocessing-or-os-fork


def connect(uri):
    engine = create_engine(uri, echo=True)
    Session = sessionmaker(bind=engine)

    Base.metadata.create_all(engine)

    return engine, Session


engine, Session = connect(config.DATABASE_URI)


@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


def get_guild(session, id):
    guild = session.query(Guild).get(id)
    if not guild:
        raise GuildNotFoundError
    return guild
