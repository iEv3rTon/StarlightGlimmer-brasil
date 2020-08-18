from sqlalchemy import Column, Integer, Float, CheckConstraint

from objects.database_models import Base


class Version(Base):
    __tablename__ = "version"

    id = Column(Integer, primary_key=True)
    version = Column(Float, nullable=False)

    CheckConstraint("id = 1")

    def __repr__(self):
        return "<Version(id={0.id}, version={0.version})>".format(self)
