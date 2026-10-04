const API = {
  async _req(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { const j = await r.json(); msg = j.detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  },
  system() { return this._req("/api/system"); },
  preview(level_id, actions) {
    return this._req("/api/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, actions }),
    });
  },
  // 执行任务：返回模拟结果 + 一次性 submit_token（不落库）
  run(level_id, actions) {
    return this._req("/api/run", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, actions }),
    });
  },
  // 幂等提交：同一 submit_token / 完全相同的动作只产生一条成绩记录
  submit(level_id, actions, submit_token) {
    return this._req("/api/submit", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, actions, submit_token }),
    });
  },
  record(id, level_id) {
    const qs = level_id ? `?level_id=${level_id}` : "";
    return this._req(`/api/records/${id}${qs}`);
  },
  records(level_id) {
    return this._req(`/api/levels/${level_id}/records`);
  },
  // 旧版接口（仅保留给老客户端兼容，新流程使用 run + submit）
  saveScore(level_id, stars, fuel_used, elapsed_days) {
    return this._req("/api/score", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ level_id, stars, fuel_used, elapsed_days }),
    });
  },
};
