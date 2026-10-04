"""游戏 API：系统信息 / 动作预览 / 任务执行 / 幂等提交成绩 / 成绩记录与回放。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

from app.services import physics, records
from app.services.levels import LEVELS, LEVEL_BY_ID

router = APIRouter(prefix="/api")


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


class SubmitRequest(BaseModel):
    level_id: int
    actions: List[Action] = Field(default_factory=list)
    submit_token: Optional[str] = Field(default=None, max_length=128)


class ScoreRequest(BaseModel):
    level_id: int
    stars: int = Field(ge=0, le=3)
    fuel_used: float = 0.0
    elapsed_days: float = 0.0


def _action_dicts(items: List[Action]) -> List[dict]:
    return [a.model_dump(exclude_none=True) for a in items]


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
    return {"bodies": bodies, "levels": levels, "scores": records.best_map()}


@router.post("/preview")
def preview(req: SimRequest):
    """实时预览：不评分、不颁发令牌、不落库。"""
    if LEVEL_BY_ID.get(req.level_id) is None:
        raise HTTPException(404, "关卡不存在")
    return records.simulate(req.level_id, _action_dicts(req.actions), with_stars=False)


@router.post("/run")
def run(req: SimRequest):
    """执行任务：服务端模拟 + 评分，并颁发一次性 submit_token 供 /api/submit。"""
    if LEVEL_BY_ID.get(req.level_id) is None:
        raise HTTPException(404, "关卡不存在")
    sim = records.simulate(req.level_id, _action_dicts(req.actions), with_stars=True)
    sim["submit_token"] = records.issue_token()
    return sim


@router.post("/submit")
def submit(req: SubmitRequest):
    """幂等提交成绩：服务端复算复评，重复 submit_token / 相同动作只落一条记录。"""
    return records.submit_run(req.level_id, _action_dicts(req.actions), req.submit_token)


@router.post("/score")
def save_score(req: ScoreRequest):
    """旧版接口兼容：接受客户端自报分数（不可回放，仅作 legacy 记录）。"""
    return records.save_legacy_score(req.level_id, req.stars,
                                     req.fuel_used, req.elapsed_days)


@router.get("/levels/{level_id}/records")
def level_records(level_id: int, limit: int = 20):
    """关卡成绩榜：按 星级↓ 燃料↑ 耗时↑ 排序，默认前 20 条（不含轨迹）。"""
    return records.list_records(level_id, limit=max(1, min(100, limit)))


@router.get("/records/{record_id}")
def record_detail(record_id: int, level_id: Optional[int] = None):
    """成绩记录详情：完整动作 + 轨迹 + 事件 + 里程碑，供前端轨迹回放。"""
    rec = records.get_record(record_id, level_id)
    return records.serialize(rec, with_trajectory=True)
