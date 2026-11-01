<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from 'vue'
import type { MachineDay } from '../lib/types'
import { fetchMachineDay } from '../lib/api'

// Every registered repo's last 24 hours against limits.yaml's max_daily_cost
// (parallel-worktree-runs decisions 73 to 82). In the account panel, only when
// the file sets a cap or can't be read.
const day = shallowRef<MachineDay | null>(null)
let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (inflight) return
  inflight = true
  try {
    day.value = await fetchMachineDay()
  } catch {
    // keep the last reading; the panel's own poll shows the API is down
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 10_000)
})

onUnmounted(() => clearInterval(timer))

const share = computed(() => (day.value?.cap ? day.value.spent / day.value.cap : 0))
const level = computed(() => (share.value >= 1 ? 'high' : share.value >= 0.8 ? 'mid' : 'low'))
const dollars = (n: number) => `$${n.toFixed(2)}`
const clears = computed(() =>
  day.value?.clears_at ? `${day.value.clears_at.slice(0, 16).replace('T', ' ')} UTC` : '',
)
const shares = computed(() => (day.value?.repos ?? []).filter((r) => r.spent > 0))
const tip = computed(() => `max_daily_cost in ${day.value?.path ?? 'limits.yaml'}. Calls that ended in the last 24 hours, in every registered repo. A call still running counts once it ends.`)
</script>

<template>
  <div v-if="day && (day.cap !== null || day.error)" class="machine" :class="level" :title="tip">
    <template v-if="day.cap !== null">
      <div class="line">
        <b class="spent">{{ dollars(day.spent) }}</b> of {{ dollars(day.cap) }} across every repo in the last 24 hours
      </div>
      <div class="bar"><div class="fill" :style="{ width: `${Math.min(share, 1) * 100}%` }" /></div>
      <div v-if="shares.length" class="repos dim">
        <span v-for="r in shares" :key="r.name">{{ r.name }} {{ dollars(r.spent) }}</span>
      </div>
      <div v-if="clears" class="clears">runs on this machine stop until {{ clears }}</div>
    </template>
    <div v-else class="clears">{{ day.path }} can't be read, so runs stop: {{ day.error }}</div>
  </div>
</template>

<style scoped>
.machine {
  font-size: 15px;
  margin-top: 14px;
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

.repos {
  margin-top: 4px;
  font-size: 14px;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}

.clears {
  margin-top: 4px;
  font-size: 14px;
}
</style>
