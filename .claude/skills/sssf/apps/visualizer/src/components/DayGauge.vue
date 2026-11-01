<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from 'vue'
import type { DaySpend } from '../lib/types'
import { fetchDay } from '../lib/api'

// The last 24 hours against repo.yaml's max_daily_cost (parallel-worktree-runs
// decisions 58 to 65). On a project card and atop its runs list.
const props = defineProps<{ project: string }>()

const day = shallowRef<DaySpend | null>(null)
let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

// Slow on purpose: a finished call moves the number once.
async function tick() {
  if (inflight) return
  inflight = true
  try {
    day.value = await fetchDay(props.project)
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

// The account panel's colours: amber from 80%, red at the cap, where runs stop.
const share = computed(() => (day.value?.cap ? day.value.spent / day.value.cap : 0))
const level = computed(() => (share.value >= 1 ? 'high' : share.value >= 0.8 ? 'mid' : 'low'))
const dollars = (n: number) => `$${n.toFixed(2)}`
// The time the stop message gives, in UTC as the trace keeps it.
const clears = computed(() =>
  day.value?.clears_at ? `${day.value.clears_at.slice(0, 16).replace('T', ' ')} UTC` : '',
)
const TIP = 'Calls that ended in the last 24 hours. A call still running counts once it ends.'
</script>

<template>
  <div v-if="day" class="day" :class="level" :title="TIP">
    <div class="line">
      <template v-if="day.cap !== null">
        <b class="spent">{{ dollars(day.spent) }}</b> of {{ dollars(day.cap) }} in the last 24 hours
      </template>
      <template v-else>
        <b>{{ dollars(day.spent) }}</b> in the last 24 hours, <span class="dim">no daily cap</span>
      </template>
    </div>
    <div v-if="day.cap !== null" class="bar">
      <div class="fill" :style="{ width: `${Math.min(share, 1) * 100}%` }" />
    </div>
    <div v-if="clears" class="clears">runs stop until {{ clears }}</div>
  </div>
</template>

<style scoped>
.day {
  font-size: 15px;
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
.clears { color: var(--red); }

.clears {
  margin-top: 4px;
  font-size: 14px;
}
</style>
