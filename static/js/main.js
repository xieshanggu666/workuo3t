const { createApp } = Vue;

const app = createApp({
  data() {
    return {
      view: "levels",
      levelId: null,
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
    enter(id) {
      if (id > 5) return;
      this.levelId = id;
      this.view = "game";
    },
    back() { this.view = "levels"; },
    onScore(s) {
      if (s.stars > (this.scores[s.level_id] || { stars: 0 }).stars) {
        this.scores = { ...this.scores, [s.level_id]: s };
      }
    },
  },
  template: `
    <LevelSelect v-if="view === 'levels'" :system="system" :scores="scores" @enter="enter" />
    <GameView v-else-if="view === 'game' && level"
              :key="levelId"
              :level="level" :bodies="system.bodies" :scores="scores"
              @back="back" @enter="enter" @score="onScore" />
  `,
});

app.mount("#app");
