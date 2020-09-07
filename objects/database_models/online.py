from sqlalchemy import Column, Integer, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from objects.database_models import Base


class Online(Base):
    __tablename__ = "online_records"

    id = Column(Integer, primary_key=True)
    time = Column(DateTime, nullable=False)
    count = Column(Integer, nullable=False)
    canvas_id = Column(Integer, ForeignKey("canvases.id", ondelete="CASCADE"), nullable=False)

    canvas = relationship("Canvas", back_populates="online_records", uselist=False)

    def __repr__(self):
        return "<OnlineRecord(id={0.id}, time={0.time}, count={0.count})>".format(self)
