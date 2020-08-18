import time

from sqlalchemy import Column, Integer, BigInteger
from sqlalchemy.schema import UniqueConstraint

from objects.database_models import Base


def current_time():
    return int(time.time())


class MenuLock(Base):
    __tablename__ = "menu_locks"

    id = Column(Integer, primary_key=True)
    channel_id = Column(BigInteger, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    date_added = Column(Integer, nullable=False, default=current_time)

    channel_user_unique = UniqueConstraint(channel_id, user_id)

    def __repr__(self):
        return ("<MenuLock(id={0.id}, channel_id={0.channel_id}, "
                "user_id={0.user_id}, date_added={0.date_added})>".format(self))
