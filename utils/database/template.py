from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.schema import UniqueConstraint

from utils.database import Base


class Template(Base):
    __tablename__ = "templates"

    id =            Column(Integer, primary_key=True)
    guild_id =      Column(Integer, ForeignKey("guilds.id"), nullable=False)
    name =          Column(String(32), nullable=False)
    url =           Column(String(100), nullable=False)
    canvas =        Column(String(32), nullable=False)
    x =             Column(Integer, nullable=False)
    y =             Column(Integer, nullable=False)
    width =         Column(Integer, nullable=False)
    height =        Column(Integer, nullable=False)
    size =          Column(Integer, nullable=False)
    date_added =    Column(Integer, nullable=False)  # timestamp
    date_modified = Column(Integer, nullable=False)  # timestamp
    md5 =           Column(String(32), nullable=False)
    owner =         Column(Integer, nullable=False)
    private =       Column(Boolean, default=False, nullable=False)
    alert_id =      Column(Integer, default=None)

    guild_templates_unique = UniqueConstraint(guild_id, name)

    guild = relationship("Guild", back_populates="templates", uselist=False)
    template_mute = relationship("MutedTemplate", back_populates="template", uselist=False)

    @property
    def center(self):
        return (2 * self.x + self.width) // 2, (2 * self.y + self.height) // 2

    def __repr__(self):
        return ("<Template(id={0.id}, guild={0.guild}, name={0.name}, "
                "url={0.url}, canvas={0.canvas}, x={0.x}, y={0.y}, w={0.width}, "
                "h={0.height}, size={0.size}, date_added={0.date_added}, "
                "date_modified={0.date_modified}, owner={0.owner}, private={0.private}, "
                "alert_id={0.alert_id})>".format(self))
