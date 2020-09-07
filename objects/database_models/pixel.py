import datetime

from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from objects.database_models import Base


def now():
    return datetime.datetime.now(datetime.timezone.utc)


class Pixel(Base):
    __tablename__ = "pixels"

    id = Column(Integer, primary_key=True)
    x = Column(Integer, nullable=False)
    y = Column(Integer, nullable=False)
    color = Column(Integer, nullable=False)
    placed = Column(DateTime, nullable=False, default=now)
    canvas_id = Column(Integer, ForeignKey("canvases.id", ondelete="CASCADE"), nullable=False)

    canvas = relationship("Canvas", back_populates="pixels", uselist=False)

    def __repr__(self):
        return "<Pixel(id={0.id}, x={0.x}, y={0.y}, color={0.color}, placed={0.placed})>".format(self)
