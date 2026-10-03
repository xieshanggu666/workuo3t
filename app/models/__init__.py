from sqlalchemy import Column, Integer, Float, UniqueConstraint

from app.core.database import Base


class LevelScore(Base):
    __tablename__ = "level_score"
    __table_args__ = (UniqueConstraint("level_id", name="uq_level"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    level_id = Column(Integer, nullable=False, index=True)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)
