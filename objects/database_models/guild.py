from sqlalchemy import Column, Integer, String, Boolean, BigInteger
from sqlalchemy.orm import relationship

from objects.database_models import Base


class Guild(Base):
    __tablename__ = "guilds"

    id =             Column(BigInteger, primary_key=True)
    name =           Column(String(100), nullable=False)
    join_date =      Column(Integer, nullable=False)  # timestamp
    prefix =         Column(String(5), default=None)
    alert_channel =  Column(Integer, default=None)
    autoscan =       Column(Boolean, default=True, nullable=False)
    canvas =         Column(String(32), default="pixelcanvas", nullable=False)
    language =       Column(String(16), default="en-us", nullable=False)
    template_admin = Column(BigInteger, default=None)
    template_adder = Column(BigInteger, default=None)
    bot_admin =      Column(BigInteger, default=None)
    faction_name =   Column(String(32), default=None)
    faction_alias =  Column(String(5), default=None)
    faction_color =  Column(Integer, default=13594340, nullable=False)
    faction_desc =   Column(String(240), default=None)
    faction_emblem = Column(String(100), default=None)
    faction_invite = Column(String(100), default=None)
    faction_hidden = Column(Boolean, default=False, nullable=False)

    # Dynamic so we can filter using sql instead of python
    # see: https://docs.sqlalchemy.org/en/13/orm/collections.html#dynamic-relationship-loaders
    templates = relationship(
        "Template", back_populates="guild", lazy="dynamic",
        cascade="all, delete, delete-orphan")

    @property
    def is_faction(self):
        return self.faction_name is not None

    def __repr__(self):
        return ("<Guild(id={0.id}, name={0.name}, prefix={0.prefix}, "
                "autoscan={0.autoscan}, canvas={0.canvas}, language={0.language})>".format(self))
