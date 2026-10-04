/* 视图：关卡选择 */
window.LevelSelect = {
  name: "LevelSelect",
  props: ["system", "scores"],
  emits: ["enter"],
  methods: {
    scoreOf(id) { return (this.scores && this.scores[id]) || null; },
    starOf(id) { const s = this.scoreOf(id); return s ? s.stars : 0; },
    unlocked(id) {
      if (id === 1) return true;
      return this.starOf(id - 1) >= 1;
    },
    enter(lv) { this.$emit("enter", lv.id, null); },
    replay(lv) {
      const s = this.scoreOf(lv.id);
      if (s && s.best_record_id) this.$emit("enter", lv.id, s.best_record_id);
    },
  },
  computed: {
    levels() {
      return (this.system && this.system.levels) || [];
    },
    bodies() {
      return (this.system && this.system.bodies) || [];
    },
    bodyById() {
      const m = {};
      for (const b of this.bodies) m[b.id] = b;
      return m;
    },
    totalStars() {
      let s = 0;
      for (const lv of this.levels) s += this.starOf(lv.id);
      return s;
    },
  },
  template: `
  <div class="screen levels-screen">
    <header class="topbar">
      <div class="logo">
        <div class="logo-mark">✦</div>
        <div>
          <h1>引力跳板</h1>
          <p>星际弹弓轨道规划 · Gravity Slingshot</p>
        </div>
      </div>
      <div class="topbar-right">
        <span class="star-sum" title="总星数">★ {{ totalStars }} / 15</span>
      </div>
    </header>

    <div class="level-grid">
      <div v-for="lv in levels" :key="lv.id"
           class="level-card" :class="{ locked: !unlocked(lv.id) }"
           @click="unlocked(lv.id) && enter(lv)">
        <div class="level-num">第 {{ lv.id }} 关</div>
        <h2>{{ lv.name }}</h2>
        <p class="brief">{{ lv.brief }}</p>
        <div class="level-foot">
          <span class="stars">
            <template v-for="i in 3" :key="i">
              <span :class="i <= starOf(lv.id) ? 'lit' : 'dim'">★</span>
            </template>
            <span v-if="scoreOf(lv.id)" class="best-meta">
              {{ (scoreOf(lv.id).fuel_used * 1731.5).toFixed(1) }} km/s
              · {{ Math.round(scoreOf(lv.id).elapsed_days) }} 天
            </span>
          </span>
          <span v-if="!unlocked(lv.id)" class="lock-tag">🔒 先通关上一关</span>
          <span v-else-if="scoreOf(lv.id)" class="foot-btns">
            <button class="btn chip" @click.stop="replay(lv)">▶ 回放最佳</button>
            <span class="go-btn" @click.stop="enter(lv)">重新规划 →</span>
          </span>
          <span v-else class="go-btn">开始规划 →</span>
        </div>
      </div>
    </div>
  </div>`,
};
