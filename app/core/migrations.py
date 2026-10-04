"""数据库初始化与旧存档迁移（SQLite，无外部迁移框架）。

- create_all：新库直接按当前模型建表；旧库缺表时补建。
- _migrate_legacy：旧版 level_score 表缺少 best_record_id 等列时用
  ALTER TABLE 补齐，并为历史最佳成绩回填一条可追溯占位记录
  （run_id = legacy:<id>，无轨迹/动作，回放端点会如实标注）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.core.database import Base, engine, SessionLocal
from app.models import LevelScore, ScoreRecord


def _column_names(conn, table: str) -> set[str]:
    try:
        return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _migrate_legacy() -> None:
    with engine.begin() as conn:
        cols = _column_names(conn, "level_score")
        if cols:
            if "best_record_id" not in cols:
                conn.execute(text("ALTER TABLE level_score ADD COLUMN best_record_id INTEGER"))
            if "best_fuel_used" not in cols:
                conn.execute(text("ALTER TABLE level_score ADD COLUMN best_fuel_used FLOAT"))
            if "best_elapsed_days" not in cols:
                conn.execute(text("ALTER TABLE level_score ADD COLUMN best_elapsed_days FLOAT"))
            if "created_at" not in cols:
                conn.execute(text("ALTER TABLE level_score ADD COLUMN created_at DATETIME"))
            if "updated_at" not in cols:
                conn.execute(text("ALTER TABLE level_score ADD COLUMN updated_at DATETIME"))

    # 为历史最佳成绩回填占位记录（独立事务，引用新表）
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        changed = False
        for row in db.query(LevelScore).all():
            row.best_fuel_used = row.best_fuel_used if row.best_fuel_used is not None else row.fuel_used
            row.best_elapsed_days = (
                row.best_elapsed_days if row.best_elapsed_days is not None else row.elapsed_days
            )
            if row.created_at is None:
                row.created_at = now
                changed = True
            # 零星空行（无实际最佳成绩）不回填占位记录，best_record_id 保持 NULL
            if row.best_record_id is not None or row.stars <= 0:
                continue
            row.updated_at = now
            rec = ScoreRecord(
                level_id=row.level_id,
                run_id=f"legacy:{row.id}",
                stars=row.stars,
                fuel_used=row.fuel_used,
                elapsed_days=row.elapsed_days,
                ok=1,
                reason="历史最佳成绩（旧版存档未保存轨迹）",
                actions_json="[]",
                events_json="[]",
                milestones_json="[]",
                trajectory_json="[]",
                created_at=now,
            )
            db.add(rec)
            db.flush()
            row.best_record_id = rec.id
        if changed:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _migrate_legacy()
