"""成绩记录服务：动作校验 / 服务端复算 / 幂等提交 / 最佳成绩晋级 / 记录序列化。

并发与幂等策略
-------------
1. 每次「执行任务」(/api/run) 颁发一次性 submit_token；提交时令牌唯一约束保证
   同一任务无论重发多少次只产生一条记录（数据库 UNIQUE(run_id)）。
2. 未携带令牌的提交退化为「规范化动作序列」内容哈希，完全相同的计划不会重复入库。
3. 最佳成绩更新用单条条件 UPDATE ... WHERE 新成绩严格优于旧成绩，配合进程锁串行化，
   在 SQLite（单写者）下并发提交也不会出现“较差成绩覆盖较好成绩”。

排名规则：星级降序 → 燃料升序 → 耗时升序。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime
from typing import Optional, Sequence

from fastapi import HTTPException
from sqlalchemy import text

from app.core.database import SessionLocal
from app.models import LevelScore, ScoreRecord
from app.services import physics
from app.services.levels import LEVEL_BY_ID

_PLANET_IDS = {b["id"] for b in physics.BODIES if b["id"] != "sun"}
_ACTION_TYPES = {"coast", "burn", "slingshot"}

# 排名并列时允许的浮点容差
_EPS = 1e-9

# 串行化写事务（SQLite 单写者 + 多 worker / 多线程兜底）
_submit_lock = threading.RLock()


# ---------------------------------------------------------------- 动作校验 / 模拟

def validate_actions(actions: Sequence[dict]) -> list[dict]:
    """规范化动作，非法参数直接 400。返回的副本即权威动作（用于入库与回放）。"""
    out: list[dict] = []
    for a in actions:
        t = (a or {}).get("type")
        if t not in _ACTION_TYPES:
            raise HTTPException(400, f"未知动作类型: {t}")
        if t == "coast":
            out.append({"type": "coast", "days": max(0.0, float(a.get("days") or 0.0))})
        elif t == "burn":
            dv = max(0.0, min(0.02, float(a.get("dv") or 0.0)))
            out.append({"type": "burn", "angle": float(a.get("angle") or 0.0), "dv": dv})
        else:  # slingshot
            pid = a.get("planet_id")
            if pid not in _PLANET_IDS:
                raise HTTPException(400, f"未知行星: {pid}")
            out.append({"type": "slingshot", "planet_id": pid,
                        "b": max(-0.2, min(0.2, float(a.get("b") or 0.0)))})
    return out


def simulate(level_id: int, actions: Sequence[dict], with_stars: bool = True) -> dict:
    """服务端权威模拟：所有分数只能由这里产生，客户端只负责编排。"""
    lv = LEVEL_BY_ID.get(level_id)
    if lv is None:
        raise HTTPException(404, "关卡不存在")
    norm = validate_actions(actions)
    r = physics.integrate(lv, norm)
    resp = {
        "ok": r["ok"],
        "reason": r["reason"],
        "elapsed_days": r["elapsed_days"],
        "fuel_used": r["fuel_used"],
        "budget_dv": r["budget_dv"],
        "milestones": r["milestones"],
        "events": r["events"],
        "trajectory": r["trajectory"],
        "actions": norm,
    }
    if with_stars:
        resp["stars"] = physics.stars_for(lv, r["fuel_used"], r["ok"])
    return resp


# ---------------------------------------------------------------- 令牌 / 内容哈希

def issue_token() -> str:
    """颁发一次性提交令牌（32 hex ≈ 128 bit）。"""
    return secrets.token_hex(16)


def _content_run_id(level_id: int, actions: Sequence[dict]) -> str:
    """无令牌时的幂等键：关卡 + 规范化动作的确定性哈希。"""
    raw = json.dumps({"l": level_id, "a": actions}, sort_keys=True, separators=(",", ":"))
    return "c:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 最佳成绩晋级

def _ensure_score_row(conn, level_id: int) -> None:
    """INSERT OR IGNORE 占位行（uq_level 唯一），保证后续 UPDATE 命中。"""
    conn.execute(text("""
        INSERT OR IGNORE INTO level_score
            (level_id, stars, fuel_used, elapsed_days,
             best_record_id, best_fuel_used, best_elapsed_days, created_at, updated_at)
        VALUES (:lid, 0, 0, 0, NULL, NULL, NULL, :now, :now)
    """), {"lid": level_id, "now": datetime.utcnow()})


def _promote_best(conn, level_id: int, record_id: int,
                  stars: int, fuel: float, elapsed: float) -> bool:
    """条件 UPDATE：仅当新记录严格优于现有最佳时才更新，返回是否晋级成功。

    旧库可能存在 best_record_id 未回填的行，此时也允许首次写入。
    排名：星级↓ → 燃料↑ → 耗时↑。
    """
    res = conn.execute(text("""
        UPDATE level_score
           SET stars = :stars,
               fuel_used = :fuel,
               elapsed_days = :elapsed,
               best_record_id = :rid,
               best_fuel_used = :fuel,
               best_elapsed_days = :elapsed,
               updated_at = :now
         WHERE level_id = :lid
           AND ( best_record_id IS NULL
                 OR :stars > stars
                 OR (:stars = stars AND :fuel < best_fuel_used - :eps)
                 OR (:stars = stars AND ABS(:fuel - best_fuel_used) <= :eps
                     AND :elapsed < best_elapsed_days - :eps) )
    """), {"lid": level_id, "rid": record_id, "stars": stars, "fuel": fuel,
           "elapsed": elapsed, "eps": _EPS, "now": datetime.utcnow()})
    return res.rowcount > 0


# ---------------------------------------------------------------- 提交（幂等核心）

def submit_run(level_id: int, actions: Sequence[dict],
               submit_token: Optional[str] = None) -> dict:
    """执行任务并落库成绩记录。

    - 服务端重新模拟 + 评分，拒绝客户端自报分数；
    - submit_token 相同 = 同一任务的网络重发，直接回放首次结果（幂等）；
    - 无令牌时按内容哈希去重；
    - 只有成功任务才进成绩榜；失败结果只回放、不留痕。
    """
    if LEVEL_BY_ID.get(level_id) is None:
        raise HTTPException(404, "关卡不存在")
    sim = simulate(level_id, actions, with_stars=True)

    if not sim["ok"]:
        return {"simulation": sim, "record": None, "is_best": False, "duplicate": False}

    norm = sim["actions"]
    run_id = submit_token or _content_run_id(level_id, norm)
    if len(run_id) > 128:
        raise HTTPException(400, "submit_token 过长（上限 128 字符）")

    with _submit_lock:
        db = SessionLocal()
        try:
            existing = db.query(ScoreRecord).filter(ScoreRecord.run_id == run_id).first()
            if existing is not None:
                if existing.level_id != level_id:
                    raise HTTPException(409, "提交令牌已用于其他关卡，请重新执行任务")
                rec, duplicate = existing, True
            else:
                rec = ScoreRecord(
                    level_id=level_id,
                    run_id=run_id,
                    stars=sim["stars"],
                    fuel_used=sim["fuel_used"],
                    elapsed_days=sim["elapsed_days"],
                    ok=1,
                    reason=sim["reason"],
                    actions_json=json.dumps(norm, ensure_ascii=False),
                    events_json=json.dumps(sim["events"], ensure_ascii=False),
                    milestones_json=json.dumps(sim["milestones"], ensure_ascii=False),
                    trajectory_json=json.dumps(sim["trajectory"], separators=(",", ":")),
                    created_at=datetime.utcnow(),
                )
                db.add(rec)
                db.flush()
                _ensure_score_row(db.connection(), level_id)
                _promote_best(db.connection(), level_id, rec.id,
                              sim["stars"], sim["fuel_used"], sim["elapsed_days"])
                duplicate = False
                try:
                    db.commit()
                except Exception:
                    # 并发下另一请求已插入同 run_id：回退为幂等回放，不重复晋级
                    db.rollback()
                    winner = db.query(ScoreRecord).filter(ScoreRecord.run_id == run_id).first()
                    if winner is None:
                        raise HTTPException(500, "成绩写入失败，请重试")
                    if winner.level_id != level_id:
                        raise HTTPException(409, "提交令牌已用于其他关卡，请重新执行任务")
                    rec, duplicate = winner, True

            rid = rec.id
            record_json = serialize(rec, with_trajectory=True)
            score = db.query(LevelScore).filter(LevelScore.level_id == level_id).first()
            best_stars = score.stars if score else 0
            is_best = score is not None and score.best_record_id == rid
        finally:
            db.close()

    return {
        "simulation": sim,
        "record": record_json,
        "is_best": is_best,
        "best_stars": best_stars,
        "duplicate": duplicate,
        "run_id": run_id,
    }


# ---------------------------------------------------------------- 旧接口兼容

def save_legacy_score(level_id: int, stars: int,
                      fuel_used: float, elapsed_days: float) -> dict:
    """兼容旧版 POST /api/score：客户端自报分数。

    旧客户端没有动作/轨迹，只能生成不可回放的 legacy 记录；同样走条件晋级，
    并发提交不会覆盖更好成绩。
    """
    if LEVEL_BY_ID.get(level_id) is None:
        raise HTTPException(404, "关卡不存在")
    with _submit_lock:
        db = SessionLocal()
        try:
            with db.begin():
                conn = db.connection()
                _ensure_score_row(conn, level_id)
                rid = None
                if stars > 0:
                    rec = ScoreRecord(
                        level_id=level_id,
                        run_id="legacy:" + secrets.token_hex(12),
                        stars=stars, fuel_used=fuel_used, elapsed_days=elapsed_days,
                        ok=1, reason="旧版客户端提交（无轨迹）",
                        actions_json="[]", events_json="[]", milestones_json="[]",
                        trajectory_json="[]", created_at=datetime.utcnow(),
                    )
                    db.add(rec)
                    db.flush()
                    rid = rec.id
                    _promote_best(conn, level_id, rid, stars, fuel_used, elapsed_days)
            score = db.query(LevelScore).filter(LevelScore.level_id == level_id).first()
            return {
                "saved": True,
                "stars": score.stars,
                "record_id": score.best_record_id,
                "is_best": rid is not None and score.best_record_id == rid,
            }
        finally:
            db.close()


# ---------------------------------------------------------------- 查询 / 序列化

def serialize(rec: ScoreRecord, with_trajectory: bool = True) -> dict:
    data = {
        "id": rec.id,
        "level_id": rec.level_id,
        "run_id": rec.run_id,
        "stars": rec.stars,
        "fuel_used": rec.fuel_used,
        "elapsed_days": rec.elapsed_days,
        "ok": bool(rec.ok),
        "reason": rec.reason,
        "replayable": bool(rec.actions_json and rec.actions_json != "[]"
                           and rec.trajectory_json and rec.trajectory_json != "[]"),
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "actions": json.loads(rec.actions_json or "[]"),
        "events": json.loads(rec.events_json or "[]"),
        "milestones": json.loads(rec.milestones_json or "[]"),
    }
    if with_trajectory:
        data["trajectory"] = json.loads(rec.trajectory_json or "[]")
    return data


def get_record(record_id: int, level_id: Optional[int] = None) -> ScoreRecord:
    db = SessionLocal()
    try:
        q = db.query(ScoreRecord).filter(ScoreRecord.id == record_id)
        if level_id is not None:
            q = q.filter(ScoreRecord.level_id == level_id)
        rec = q.first()
        if rec is None:
            raise HTTPException(404, "成绩记录不存在")
        db.expunge(rec)
        return rec
    finally:
        db.close()


def list_records(level_id: int, limit: int = 20) -> dict:
    """按排名（星级↓ 燃料↑ 耗时↑）返回关卡成绩列表。"""
    if LEVEL_BY_ID.get(level_id) is None:
        raise HTTPException(404, "关卡不存在")
    db = SessionLocal()
    try:
        rows = (db.query(ScoreRecord)
                .filter(ScoreRecord.level_id == level_id, ScoreRecord.ok == 1)
                .order_by(ScoreRecord.stars.desc(),
                          ScoreRecord.fuel_used.asc(),
                          ScoreRecord.elapsed_days.asc(),
                          ScoreRecord.id.asc())
                .limit(limit).all())
        best = db.query(LevelScore).filter(LevelScore.level_id == level_id).first()
        return {
            "level_id": level_id,
            "best_record_id": best.best_record_id if best else None,
            "records": [serialize(r, with_trajectory=False) for r in rows],
        }
    finally:
        db.close()


def best_map() -> dict:
    """level_id -> 最佳成绩摘要（含 best_record_id，供 /api/system 与前端回放）。"""
    db = SessionLocal()
    try:
        out = {}
        for s in db.query(LevelScore).all():
            if s.best_record_id is None:
                continue
            out[s.level_id] = {
                "stars": s.stars,
                "fuel_used": s.fuel_used,
                "elapsed_days": s.elapsed_days,
                "best_record_id": s.best_record_id,
            }
        return out
    finally:
        db.close()
