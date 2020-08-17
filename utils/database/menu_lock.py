from sqlalchemy import Column, Integer
from sqlalchemy.schema import UniqueConstraint

from utils.database import Base


class MenuLock(Base):
    __tablename__ = "menu_locks"

    id = Column(Integer, primary_key=True)
    channel_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    date_added = Column(Integer, nullable=False)

    channel_user_unique = UniqueConstraint(channel_id, user_id)

    def __repr__(self):
        return ("<MenuLock(id={0.id}, channel_id={0.channel_id}, "
                "user_id={0.user_id}, date_added={0.date_added})>".format(self))
