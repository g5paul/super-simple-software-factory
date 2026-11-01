<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from 'vue'
import type { LiveSpend } from '../lib/types'
import { fetchLive } from '../lib/api'
import { projectHref } from '../lib/router'

// What the live runs spend together against repo.yaml's max_live_cost
// (parallel-worktree-runs decisions 66 to 72). Under the 24-hour gauge, on a
// project card and atop its runs list. `links` is off on the card, which is
// itself a link; the runs list links each run.
const props = defineProps<{ project: string; links?: boolean }>()

const live = shallowRef<LiveSpend | null>(null)
let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (inflight) return
  inflight = true
  try {
    live.value = await fetchLive(props.project)
  } catch {
    // keep the last reading; the page's own poll shows the API is down
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 10_000)
})

onUnmounted(() => clearInterval(timer))

// Nothing to say for an idle repo with no cap.
const shown = computed(() => !!live.value && (live.value.cap !== null || live.value.runs.length > 0))
const share = computed(() => (live.value?.cap ? live.value.spent / live.value.cap : 0))
const level = computed(() => (share.value >= 1 ? 'high' : share.value >= 0.8 ? 'mid' : 'low'))
const dollars = (n: number) => `$${n.toFixed(2)}`
const across = computed(() => {
  const n = live.value?.runs.length ?? 0
  return `across ${n} live run${n === 1 ? '' : 's'}`
})
const runHref = (adwId: string) => `${projectHref(props.project)}/${encodeURIComponent(adwId)}`
const TIP = "Each live run's current attempt: calls that ended since its process started. A call still running counts once it ends."
</script>

<template>
  <div v-if="shown && live" class="live" :class="level" :title="TIP">
    <div class="line">
      <template v-if="live.cap !== null">
        <b class="spent">{{ dollars(live.spent) }}</b> of {{ dollars(live.cap) }} {{ across }}
      </template>
      <template v-else>
        <b>{{ dollars(live.spent) }}</b> {{ across }}, <span class="dim">no live cap</span>
      </template>
    </div>
    <div v-if="live.cap !== null" class="bar">
      <div class="fill" :style="{ width: `${Math.min(share, 1) * 100}%` }" />
    </div>
    <div v-if="live.runs.length" class="runs dim">
      <span v-for="r in live.runs" :key="r.adw_id" class="run">
        <a v-if="links" :href="runHref(r.adw_id)">{{ r.adw_id }}</a><template v-else>{{ r.adw_id }}</template>
        {{ dollars(r.cost) }}
      </span>
    </div>
    <div v-if="level === 'high'" class="stops">new runs and sends stop until a run finishes</div>
  </div>
</template>

<style scoped>
.live {
  font-size: 15px;
  margin-top: 10px;
}

.bar {
  height: 8px;
  margin-top: 6px;
  border-radius: 4px;
  background: var(--panel-2);
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 4px;
  background: var(--green);
}

.mid .fill { background: var(--amber); }
.high .fill { background: var(--red); }
.high .spent,
.stops { color: var(--red); }

.runs {
  margin-top: 4px;
  font-size: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.run a {
  color: inherit;
  text-decoration: underline;
}

.stops {
  margin-top: 4px;
  font-size: 14px;
}
</style>
