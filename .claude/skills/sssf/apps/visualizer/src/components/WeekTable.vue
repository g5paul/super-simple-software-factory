<script setup lang="ts">
import type { WeekWindow } from '../lib/types'
import { fmtCost } from '../lib/format'

// Readings are whole percents: below 10% used, the rounding swamps $ per 1%.
const FULL_FROM = 0.1

defineProps<{ weeks: WeekWindow[], label: string }>()

const when = (iso: string) => new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false })

const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })

function perPoint(w: WeekWindow): string {
  return w.usd !== null && w.peak >= FULL_FROM ? fmtCost(w.usd / (w.peak * 100)) : '—'
}

function full(w: WeekWindow): string {
  return w.usd !== null && w.peak >= FULL_FROM ? fmtCost(w.usd / w.peak) : '—'
}
</script>

<template>
  <div class="weeks">
    <div class="caption">{{ label }} week by week. A fall in $ per 1% with the same use means the plan's limit shrank.</div>
    <table>
      <thead>
        <tr><th>window</th><th>peak</th><th>spend</th><th>$ per 1%</th><th>100% ≈</th></tr>
      </thead>
      <tbody>
        <tr v-for="w in weeks" :key="w.end" :class="{ current: w.current }">
          <td>{{ day(w.start) }} to {{ day(w.end) }}{{ w.current ? ', so far' : '' }}</td>
          <td>{{ Math.round(w.peak * 100) }}%</td>
          <td>{{ w.usd === null ? 'gone' : fmtCost(w.usd) }}</td>
          <td>{{ perPoint(w) }}</td>
          <td>{{ full(w) }}</td>
        </tr>
      </tbody>
    </table>
    <div v-if="weeks.every((w) => w.current)" class="faint small">
      The first closed week lands here when this one resets, {{ when(weeks[0]!.end) }}.
    </div>
    <div v-if="weeks.some((w) => w.usd === null)" class="faint small">
      "gone": the transcripts or traces SSSF reads start after that window did, so its spend isn't known.
    </div>
  </div>
</template>

<style scoped>
.weeks { margin-top: 12px; }
.caption { font-size: 13px; color: var(--faint); margin-bottom: 4px; }
table { border-collapse: collapse; font-size: 14px; }
th { text-align: left; font-weight: 500; color: var(--faint); padding: 2px 16px 2px 0; }
td { padding: 2px 16px 2px 0; font-variant-numeric: tabular-nums; }
tr.current td { color: var(--faint); }
.small { font-size: 12px; }
.faint { color: var(--faint); }
</style>
