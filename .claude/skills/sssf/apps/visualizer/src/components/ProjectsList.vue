<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import type { ProjectSummary } from '../lib/types'
import { fetchProjects } from '../lib/api'
import { fmtDate, ts } from '../lib/format'
import { projectHref } from '../lib/router'
import AccountPanel from './AccountPanel.vue'

const projects = shallowRef<ProjectSummary[]>([])
const apiError = ref<string | null>(null)
const loaded = ref(false)

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

// Slower than the session views: this page only shows counts, and every tick
// opens each project's db.
async function tick() {
  if (inflight) return
  inflight = true
  try {
    projects.value = (await fetchProjects()).projects
    apiError.value = null
    loaded.value = true
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 2000)
})

onUnmounted(() => clearInterval(timer))

// Running projects first, then the most recent run.
const ordered = computed(() =>
  projects.value.toSorted(
    (a, b) =>
      Number(b.running > 0) - Number(a.running > 0) ||
      (ts(b.last_started_at) || 0) - (ts(a.last_started_at) || 0) ||
      a.name.localeCompare(b.name),
  ),
)
</script>

<template>
  <div class="projects">
    <div v-if="apiError" class="error-bar">api unreachable — retrying {{ apiError }}</div>

    <AccountPanel />

    <div v-if="ordered.length" class="list-head dim">{{ ordered.length }} projects</div>

    <div v-if="ordered.length" class="cards">
      <a
        v-for="p in ordered"
        :key="p.name"
        class="card"
        :class="{ running: p.running > 0 }"
        :href="projectHref(p.name)"
      >
        <div class="name">
          <span v-if="p.running > 0" class="live-dot" />
          {{ p.name }}
        </div>
        <div class="root faint">{{ p.root }}</div>
        <div v-if="p.available" class="counts">
          <span>{{ p.sessions }} runs</span>
          <span v-if="p.running > 0" class="running-count">{{ p.running }} running</span>
          <span class="dim">last {{ fmtDate(p.last_started_at) }}</span>
        </div>
        <div v-else class="counts dim">no runs yet</div>
      </a>
    </div>
    <div v-else-if="loaded" class="empty-state">
      no projects yet — install.py and install.py --update add each repo
    </div>
    <div v-else-if="!apiError" class="empty-state">loading projects…</div>
  </div>
</template>

<style scoped>
.projects {
  display: flex;
  flex-direction: column;
}

.list-head {
  padding: 16px 24px 0;
  font-size: 16px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 18px;
  padding: 16px 24px 28px;
}

.card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 18px 22px;
  border: 1px solid var(--border-soft);
  border-radius: 16px;
  background: var(--surface);
  color: var(--text);
  transition:
    border-color 0.18s ease,
    box-shadow 0.18s ease,
    transform 0.18s ease;
}

.card:hover {
  border-color: rgba(148, 163, 255, 0.45);
  box-shadow: 0 10px 34px rgba(148, 163, 255, 0.12);
  transform: translateY(-2px);
}

.card.running {
  border-color: rgba(108, 182, 255, 0.6);
  box-shadow: 0 0 22px rgba(108, 182, 255, 0.16);
}

.name {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 20px;
  font-weight: 700;
}

.root {
  font-family: var(--mono);
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.counts {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 15px;
}

.running-count {
  color: var(--blue);
}

.live-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 10px rgba(74, 222, 128, 0.7);
  animation: pulse 1.6s ease-in-out infinite;
}
</style>
