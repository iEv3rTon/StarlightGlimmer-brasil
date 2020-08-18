from sqlalchemy import Column, Integer, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.schema import UniqueConstraint

from objects.database_models import Base


class Snapshot(Base):
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True)
    base_template_id = Column(Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False)
    target_template_id = Column(Integer, ForeignKey("templates.id", ondelete="CASCADE"), nullable=False)

    snapshot_unique = UniqueConstraint(base_template_id, target_template_id)

    base_template = relationship(
        "Template",
        foreign_keys="Snapshot.base_template_id",
        uselist=False,
        back_populates="snapshot_bases",
        lazy="immediate"
    )
    target_template = relationship(
        "Template",
        foreign_keys="Snapshot.target_template_id",
        uselist=False,
        back_populates="snapshot_targets",
        lazy="immediate"
    )

    def __repr__(self):
        return ("<Snapshot(id={0.id}, base_template={0.base_template}, "
                "target_template={0.target_template})>".format(self))