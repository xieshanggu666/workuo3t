from sqlalchemy import (
    Column, Integer, Float, String, Text, DateTime, UniqueConstraint, Index,
)
from app.core.database import Base


class LevelScore(Base):
    """每关最佳成绩汇总（每关一行）。

    best_record_id 指向 score_record 中对应的可追溯记录；旧存档升级后该列为
    NULL 的行会在启动迁移中回填（生成一条无轨迹的占位记录）。
    """
    __tablename__ = "level_score"
    __table_args__ = (UniqueConstraint("level_id", name="uq_level"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    level_id = Column(Integer, nullable=False, index=True)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)

    # ---- 升级新增列（旧库通过 ALTER TABLE 补齐） ----
    best_record_id = Column(Integer, nullable=True)
    best_fuel_used = Column(Float, nullable=True)
    best_elapsed_days = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True)


class ScoreRecord(Base):
    """可追溯的单次成绩记录：完整动作计划 + 轨迹 + 事件 + 里程碑，供回放。

    run_id 唯一：正常流程使用 /api/run 颁发的 submit_token；无令牌时退化为
    “规范化动作序列”的内容哈希，使重复提交天然幂等。
    """
    __tablename__ = "score_record"

    id = Column(Integer, primary_key=True, autoincrement=True)
    level_id = Column(Integer, nullable=False)
    run_id = Column(String(128), nullable=False, unique=True)
    stars = Column(Integer, nullable=False, default=0)
    fuel_used = Column(Float, nullable=False, default=0.0)
    elapsed_days = Column(Float, nullable=False, default=0.0)
    ok = Column(Integer, nullable=False, default=0)
    reason = Column(String(128), nullable=True)
    actions_json = Column(Text, nullable=False, default="[]")
    events_json = Column(Text, nullable=False, default="[]")
    milestones_json = Column(Text, nullable=False, default="[]")
    trajectory_json = Column(Text, nullable=False, default="[]")
    created_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("ix_score_record_level_id", "level_id"),
        Index("ix_score_record_level_created", "level_id", "created_at"),
    )
