"""游戏 API：系统信息 / 动作预览 / 任务执行 / 最佳成绩存档。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Union

from app.models import LevelScore
from app.core.database import get_db
from app.services import physics
from app.services.levels import LEVELS, LEVEL_BY_ID

router = APIRouter(prefix="/api")

_PLANET_IDS = {b["id"] for b in physics.BODIES if b["id"] != "sun"}
_ACTION_TYPES = {"coast", "burn", "slingshot"}


class Action(BaseModel):
    type: str
    days: Optional[float] = None
    angle: Optional[float] = None
    dv: Optional[float] = None
    planet_id: Optional[str] = None
    b: Optional[float] = None


class SimRequest(BaseModel):
    level_id: int
    actions: List[Action] = Field(default_factory=list)


class ScoreRequest(BaseModel):
    level_id: int
    stars: int = Field(ge=0, le=3)
    fuel_used: float = 0.0
    elapsed_days: float = 0.0


def _validate(actions: List[Action]) -> List[dict]:
    out = []
    for a in actions:
        if a.type not in _ACTION_TYPES:
            raise HTTPException(400, f"未知动作类型: {a.type}")
        if a.type == "coast":
            out.append({"type": "coast", "days": max(0.0, a.days or 0.0)})
        elif a.type == "burn":
            out.append({"type": "burn", "angle": float(a.angle or 0.0),
                        "dv": max(0.0, min(0.02, a.dv or 0.0))})
        elif a.type == "slingshot":
            if a.planet_id not in _PLANET_IDS:
                raise HTTPException(400, f"未知行星: {a.planet_id}")
            out.append({"type": "slingshot", "planet_id": a.planet_id,
                        "b": max(-0.2, min(0.2, a.b or 0.0))})
    return out


def _sim_response(lv, actions, with_stars: bool):
    r = physics.integrate(lv, actions)
    resp = {
        "ok": r["ok"],
        "reason": r["reason"],
        "elapsed_days": r["elapsed_days"],
        "fuel_used": r["fuel_used"],
        "budget_dv": r["budget_dv"],
        "milestones": r["milestones"],
        "events": r["events"],
        "trajectory": r["trajectory"],
    }
    if with_stars:
        stars = physics.stars_for(lv, r["fuel_used"], r["ok"])
        resp["stars"] = stars
    return resp


@router.get("/system")
def system_info():
    bodies = []
    for b in physics.BODIES:
        if b["id"] == "sun":
            bodies.append({"id": b["id"], "name": b["name"], "color": b["color"],
                           "radius": b["radius"]})
        else:
            bodies.append({"id": b["id"], "name": b["name"], "color": b["color"],
                           "radius": b["radius"], "orbit": b["orbit"],
                           "period": b["period"], "theta0": b["theta0"],
                           "soi": physics._soi_radius(b["id"])})
    levels = [{"id": lv["id"], "name": lv["name"], "brief": lv["brief"],
               "milestones": lv["milestones"], "budget_dv": lv["budget_dv"],
               "t_max": lv["t_max"], "hint": lv["hint"]} for lv in LEVELS]
    scores = {}
    db = next(get_db())
    for s in db.query(LevelScore).all():
        scores[s.level_id] = {"stars": s.stars, "fuel_used": s.fuel_used,
                              "elapsed_days": s.elapsed_days}
    return {"bodies": bodies, "levels": levels, "scores": scores}


@router.post("/preview")
def preview(req: SimRequest):
    lv = LEVEL_BY_ID.get(req.level_id)
    if lv is None:
        raise HTTPException(404, "关卡不存在")
    return _sim_response(lv, _validate(req.actions), with_stars=False)


@router.post("/run")
def run(req: SimRequest):
    lv = LEVEL_BY_ID.get(req.level_id)
    if lv is None:
        raise HTTPException(404, "关卡不存在")
    return _sim_response(lv, _validate(req.actions), with_stars=True)


@router.post("/score")
def save_score(req: ScoreRequest):
    db = next(get_db())
    row = db.query(LevelScore).filter(LevelScore.level_id == req.level_id).first()
    if row is None:
        row = LevelScore(level_id=req.level_id, stars=req.stars,
                         fuel_used=req.fuel_used, elapsed_days=req.elapsed_days)
        db.add(row)
    elif req.stars > row.stars:
        row.stars = req.stars
        row.fuel_used = req.fuel_used
        row.elapsed_days = req.elapsed_days
    db.commit()
    return {"saved": True, "stars": max(row.stars, req.stars)}
