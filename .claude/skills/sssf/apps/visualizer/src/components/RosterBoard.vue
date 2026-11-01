<script setup lang="ts">
import { onMounted, shallowRef } from 'vue'
import type { RosterRow } from '../lib/types'
import { fetchRosters } from '../lib/api'
import { rosterLabel } from '../lib/format'

// Which roster does this repo's work best (trials.md decision 25): one row per
// roster file version, from every trial whose panel ran. Shown once there is one.
const props = defineProps<{ project: string }>()

const rows = shallowRef<RosterRow[]>([])

onMounted(async () => {
  try {
    rows.value = await fetchRosters(props.project)
  } catch {
    rows.value = []
  }
})

const dollars = (n: number | null) => (n === null ? '-' : `$${n.toFixed(2)}`)
const minutes = (s: number | null) => (s === null ? '-' : `${(s / 60).toFixed(1)}m`)
const TIP = 'From every trial whose panel ran. Medians are over the attempts that passed every gate.'
</script>

<template>
  <div v-if="rows.length" class="board" :title="TIP">
    <div class="dim head">rosters, from trials; cost and time are medians over the attempts that passed</div>
    <table>
      <thead>
        <tr>
          <th>roster</th><th>trials</th><th>passed</th><th>wins</th><th>cost</th><th>time</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="`${r.roster}-${r.sha256}`" :class="{ old: !r.current }">
          <td>
            {{ rosterLabel(r.roster, r.coding_agent) }} <code class="dim">{{ r.sha256.slice(0, 7) }}</code>
            <span v-if="r.current" class="current">current</span>
          </td>
          <td>{{ r.trials }}</td>
          <td>{{ r.passed }} of {{ r.attempts }}</td>
          <td>{{ r.wins }}</td>
          <td>{{ dollars(r.median_cost) }}</td>
          <td>{{ minutes(r.median_seconds) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.board {
  margin-top: 14px;
  font-size: 15px;
}

.head {
  margin-bottom: 4px;
}

table {
  border-collapse: collapse;
}

th,
td {
  text-align: left;
  padding: 3px 14px 3px 0;
  white-space: nowrap;
}

th {
  color: var(--faint);
  font-weight: normal;
}

.old {
  color: var(--faint);
}

.current {
  margin-left: 6px;
  color: var(--green);
  font-size: 13px;
}
</style>
