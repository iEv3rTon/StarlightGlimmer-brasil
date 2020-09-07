from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship

from objects.database_models import Base


class Canvas(Base):
    __tablename__ = "canvases"

    id = Column(Integer, primary_key=True)
    nick = Column(String, nullable=False, unique=True)
    url = Column(String, nullable=False, unique=True)

    pixels = relationship(
        "Pixel", back_populates="canvas",
        cascade="all, delete, delete-orphan")
    online_records = relationship(
        "Online", back_populates="canvas",
        cascade="all, delete, delete-orphan")

    def __repr__(self):
        return "<Canvas(id={0.id}, nick={0.nick}, url={0.url})>".format(self)
