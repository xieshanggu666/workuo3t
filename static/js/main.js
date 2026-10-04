const { createApp } = Vue;

const app = createApp({
  data() {
    return {
      view: "levels",
      levelId: null,
      replayRecordId: null,
      system: null,
      scores: {},
    };
  },
  components: { LevelSelect, GameView },
  async created() {
    try {
      this.system = await API.system();
      this.scores = this.system.scores || {};
    } catch (e) {
      this.system = null;
    }
  },
  computed: {
    level() {
      if (!this.system) return null;
      return this.system.levels.find(l => l.id === this.levelId) || null;
    },
  },
  methods: {
    enter(id, replayRecordId = null) {
      if (id > 5) return;
      this.levelId = id;
      this.replayRecordId = replayRecordId;
      this.view = "game";
    },
    back() { this.view = "levels"; this.replayRecordId = null; },
    // 新成绩提交后由服务端裁决的最佳成绩摘要回写本地
    onScore(s) {
      const cur = this.scores[s.level_id];
      const curStars = cur ? cur.stars : 0;
      if ((s.stars || 0) >= curStars) {
        this.scores = { ...this.scores, [s.level_id]: s };
      }
    },
  },
  template: `
    <LevelSelect v-if="view === 'levels'" :system="system" :scores="scores" @enter="enter" />
    <GameView v-else-if="view === 'game' && level"
              :key="levelId + '-' + (replayRecordId || 'new')"
              :level="level" :bodies="system.bodies" :scores="scores"
              :replay-record-id="replayRecordId"
              @back="back" @enter="enter" @score="onScore" />
  `,
});

app.mount("#app");
