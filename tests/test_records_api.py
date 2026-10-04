"""成绩记录与幂等提交测试。

每个用例使用独立的临时 SQLite（DB_PATH 环境变量指向临时文件），通过强制重载
app.* 模块让数据库引擎按新路径重建，避免污染开发库 data/game.db。
"""
import os
import sys
import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from app.services.levels import LEVELS  # noqa: E402


def _fresh_app(tmp_path):
    db_file = tmp_path / "test_game.db"
    os.environ["DB_PATH"] = str(db_file)
    # 清掉所有按旧 DB_PATH 初始化过的 app 模块缓存，强制重建引擎/表
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    import app.main as main_mod  # noqa: PLC0415
    import app.services.records as records_mod  # noqa: PLC0415
    client = TestClient(main_mod.app)
    return client, main_mod, records_mod, str(db_file)


@pytest.fixture()
def env(tmp_path):
    client, main_mod, records_mod, db_file = _fresh_app(tmp_path)
    yield {"c": client, "records": records_mod, "db": db_file}
    os.environ.pop("DB_PATH", None)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


REF = {lv["id"]: lv["ref"] for lv in LEVELS}


# --------------------------------------------------------------- 任务执行 + 提交

def test_run_issues_token_and_does_not_persist(env):
    """POST /api/run 只模拟+颁发令牌，不落库；提交后才出现记录。"""
    c = env["c"]
    r = c.post("/api/run", json={"level_id": 1, "actions": REF[1]})
    j = r.json()
    assert j["ok"] and j["stars"] == 3
    assert j["submit_token"] and len(j["submit_token"]) == 32
    assert c.get("/api/levels/1/records").json()["records"] == []


def test_submit_persists_record_with_trajectory_and_is_best(env):
    c = env["c"]
    tok = c.post("/api/run", json={"level_id": 1, "actions": REF[1]}).json()["submit_token"]
    s = c.post("/api/submit",
               json={"level_id": 1, "actions": REF[1], "submit_token": tok}).json()
    assert s["is_best"] is True and s["duplicate"] is False
    rec = s["record"]
    assert rec["replayable"] is True
    assert len(rec["trajectory"]) > 1
    assert rec["actions"] == [
        {"type": "burn", "angle": 90.0, "dv": 0.0017},
    ]
    assert rec["stars"] == 3
    # /api/system 汇总同步更新，并带 best_record_id
    scores = c.get("/api/system").json()["scores"]
    assert scores["1"]["best_record_id"] == rec["id"]


# --------------------------------------------------------------- 幂等：令牌 / 内容

def test_duplicate_token_is_idempotent(env):
    c = env["c"]
    tok = c.post("/api/run", json={"level_id": 1, "actions": REF[1]}).json()["submit_token"]
    body = {"level_id": 1, "actions": REF[1], "submit_token": tok}
    s1 = c.post("/api/submit", json=body).json()
    s2 = c.post("/api/submit", json=body).json()
    s3 = c.post("/api/submit", json=body).json()
    assert s1["record"]["id"] == s2["record"]["id"] == s3["record"]["id"]
    assert s1["duplicate"] is False and s2["duplicate"] is True and s3["duplicate"] is True
    assert len(c.get("/api/levels/1/records").json()["records"]) == 1


def test_content_hash_dedup_without_token(env):
    c = env["c"]
    body = {"level_id": 1, "actions": REF[1]}
    s1 = c.post("/api/submit", json=body).json()
    s2 = c.post("/api/submit", json=body).json()
    assert s1["duplicate"] is False and s2["duplicate"] is True
    assert s1["record"]["id"] == s2["record"]["id"]
    assert s1["record"]["run_id"].startswith("c:")


def test_token_reused_across_levels_conflicts(env):
    c = env["c"]
    tok = c.post("/api/run", json={"level_id": 1, "actions": REF[1]}).json()["submit_token"]
    c.post("/api/submit", json={"level_id": 1, "actions": REF[1], "submit_token": tok})
    r = c.post("/api/submit",
               json={"level_id": 2, "actions": REF[2], "submit_token": tok})
    assert r.status_code == 409


# --------------------------------------------------------------- 服务端权威评分

def test_server_recomputes_stars(env):
    """即使伪造 stars，分数也以服务端模拟为准（提交接口不接受 stars 字段）。"""
    c = env["c"]
    # 燃料几乎耗尽的成功方案：服务端判 2 星，客户端无法谎报 3 星
    actions = [{"type": "burn", "angle": 90.0, "dv": 0.0062}]
    j = c.post("/api/run", json={"level_id": 2, "actions": actions}).json()
    assert j["ok"] and j["stars"] == 2
    tok = j["submit_token"]
    s = c.post("/api/submit",
               json={"level_id": 2, "actions": actions, "submit_token": tok}).json()
    assert s["record"]["stars"] == 2 and s["best_stars"] == 2


def test_failed_run_is_not_persisted(env):
    c = env["c"]
    bad = [{"type": "burn", "angle": 90.0, "dv": 0.02}]  # 超预算
    j = c.post("/api/run", json={"level_id": 1, "actions": bad}).json()
    assert j["ok"] is False and j["stars"] == 0
    s = c.post("/api/submit", json={"level_id": 1, "actions": bad}).json()
    assert s["record"] is None and s["is_best"] is False
    assert c.get("/api/levels/1/records").json()["records"] == []
    assert c.get("/api/system").json()["scores"] == {}


# --------------------------------------------------------------- 最佳成绩晋级规则

def test_worse_record_does_not_replace_best(env):
    c = env["c"]
    # 先拿 3 星
    t3 = c.post("/api/run", json={"level_id": 2, "actions": REF[2]}).json()["submit_token"]
    best = c.post("/api/submit",
                  json={"level_id": 2, "actions": REF[2], "submit_token": t3}).json()
    assert best["best_stars"] == 3
    # 再来 2 星：记录留档，但不晋级
    worse = [{"type": "burn", "angle": 90.0, "dv": 0.0062}]
    t2 = c.post("/api/run", json={"level_id": 2, "actions": worse}).json()["submit_token"]
    s = c.post("/api/submit",
               json={"level_id": 2, "actions": worse, "submit_token": t2}).json()
    assert s["is_best"] is False and s["best_stars"] == 3
    # 成绩榜两条，按 星级↓ 燃料↑ 排序，最佳指向 3 星记录
    lst = c.get("/api/levels/2/records").json()
    assert [r["stars"] for r in lst["records"]] == [3, 2]
    assert lst["best_record_id"] == best["record"]["id"]


def test_better_record_replaces_best_and_unlocks(env):
    c = env["c"]
    # 先 2 星
    worse = [{"type": "burn", "angle": 90.0, "dv": 0.0062}]
    t2 = c.post("/api/run", json={"level_id": 2, "actions": worse}).json()["submit_token"]
    c.post("/api/submit", json={"level_id": 2, "actions": worse, "submit_token": t2})
    # 后 3 星刷新最佳
    t3 = c.post("/api/run", json={"level_id": 2, "actions": REF[2]}).json()["submit_token"]
    s = c.post("/api/submit",
               json={"level_id": 2, "actions": REF[2], "submit_token": t3}).json()
    assert s["is_best"] is True and s["best_stars"] == 3
    scores = c.get("/api/system").json()["scores"]
    assert scores["2"]["stars"] == 3


# --------------------------------------------------------------- 并发提交

def test_concurrent_token_submissions_create_single_record(env):
    """同一计划并发提交：内容哈希去重 + 唯一约束，最终只落一条记录。"""
    c = env["c"]
    errors = []

    def worker():
        try:
            c.post("/api/submit", json={"level_id": 1, "actions": REF[1]})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    rows = c.get("/api/levels/1/records").json()["records"]
    assert len(rows) == 1
    assert rows[0]["stars"] == 3


def test_concurrent_better_and_worse_best_wins(env):
    """并发提交好/差成绩，最佳成绩必须是 3 星而不是后写入的 2 星。"""
    c = env["c"]
    barrier = threading.Barrier(2)

    def good():
        barrier.wait()
        tok = c.post("/api/run", json={"level_id": 2, "actions": REF[2]}).json()["submit_token"]
        c.post("/api/submit", json={"level_id": 2, "actions": REF[2], "submit_token": tok})

    def bad():
        actions = [{"type": "burn", "angle": 90.0, "dv": 0.0062}]
        barrier.wait()
        tok = c.post("/api/run", json={"level_id": 2, "actions": actions}).json()["submit_token"]
        c.post("/api/submit", json={"level_id": 2, "actions": actions, "submit_token": tok})

    tg = threading.Thread(target=good)
    tb = threading.Thread(target=bad)
    tg.start(); tb.start(); tg.join(); tb.join()
    scores = c.get("/api/system").json()["scores"]
    assert scores["2"]["stars"] == 3


# --------------------------------------------------------------- 旧存档迁移

def test_legacy_db_migrates_and_backfills(tmp_path):
    db_file = tmp_path / "old.db"
    con = sqlite3.connect(db_file)
    con.execute("""CREATE TABLE level_score (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        level_id INTEGER NOT NULL,
        stars INTEGER NOT NULL,
        fuel_used FLOAT NOT NULL,
        elapsed_days FLOAT NOT NULL,
        CONSTRAINT uq_level UNIQUE (level_id))""")
    con.execute("INSERT INTO level_score (level_id, stars, fuel_used, elapsed_days) "
                "VALUES (1, 3, 0.0017, 142.5)")
    con.commit(); con.close()

    os.environ["DB_PATH"] = str(db_file)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    try:
        import app.main  # noqa: F401, PLC0415
        from fastapi.testclient import TestClient as _TC  # noqa: PLC0415
        client = _TC(app.main.app)  # type: ignore[name-defined]
        scores = client.get("/api/system").json()["scores"]
        assert scores["1"]["stars"] == 3
        rid = scores["1"]["best_record_id"]
        rec = client.get(f"/api/records/{rid}").json()
        assert rec["run_id"] == f"legacy:{rid}" or rec["run_id"].startswith("legacy:")
        assert rec["replayable"] is False

        # 迁移可重复执行（重启不重复回填）
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
        import app.main as m2  # noqa: PLC0415
        con = sqlite3.connect(db_file)
        n = con.execute("SELECT COUNT(*) FROM score_record WHERE run_id LIKE 'legacy:%'").fetchone()[0]
        con.close()
        assert n == 1
    finally:
        os.environ.pop("DB_PATH", None)


# --------------------------------------------------------------- 旧接口兼容

def test_legacy_score_endpoint_still_works(env):
    c = env["c"]
    r = c.post("/api/score",
               json={"level_id": 3, "stars": 2, "fuel_used": 0.008, "elapsed_days": 1000.0})
    assert r.json()["saved"] is True and r.json()["stars"] == 2
    # 更差的自报分数不能覆盖
    r2 = c.post("/api/score",
                json={"level_id": 3, "stars": 1, "fuel_used": 0.009, "elapsed_days": 2000.0})
    assert r2.json()["stars"] == 2 and r2.json()["is_best"] is False


# --------------------------------------------------------------- 记录详情 / 校验

def test_record_detail_and_validation(env):
    c = env["c"]
    # 404
    assert c.get("/api/records/999").status_code == 404
    # 非法动作 400
    r = c.post("/api/preview", json={"level_id": 1, "actions": [{"type": "warp"}]})
    assert r.status_code == 400
    r = c.post("/api/preview",
               json={"level_id": 1, "actions": [{"type": "slingshot", "planet_id": "sun"}]})
    assert r.status_code == 400
    # 不存在的关卡 404
    assert c.post("/api/run", json={"level_id": 99, "actions": []}).status_code == 404
