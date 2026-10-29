<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import type { AccountUsage, LimitWindow, SpendRow } from '../lib/types'
import { fetchAccount } from '../lib/api'
import { fmtCost, fmtDate, fmtTokens } from '../lib/format'
import { modelName } from '../lib/models'

const usage = shallowRef<AccountUsage | null>(null)
const error = ref<string | null>(null)

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

// Slow on purpose: each tick reads whatever the transcripts appended.
async function tick() {
  if (inflight) return
  inflight = true
  try {
    usage.value = await fetchAccount()
    error.value = null
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 10_000)
})

onUnmounted(() => clearInterval(timer))

// Matches agents.py's default rate_limit_stop: SSSF stops new agents from here.
const STOP_AT = 0.8

function level(w: LimitWindow | null): string {
  if (!w) return 'unknown'
  if (w.utilization >= STOP_AT) return 'high'
  if (w.utilization >= 0.5) return 'mid'
  return 'low'
}

function age(s: number): string {
  if (s < 90) return `${s}s ago`
  if (s < 5400) return `${Math.round(s / 60)}m ago`
  if (s < 172_800) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86_400)}d ago`
}

const windows = computed(() => {
  const s = usage.value?.sample
  return [
    { label: '5-hour', w: s?.five_hour ?? null },
    { label: '7-day', w: s?.seven_day ?? null },
  ]
})

function share(row: SpendRow, total: number): string {
  return total > 0 ? `${Math.round((row.est_usd / total) * 100)}%` : ''
}
</script>

<template>
  <section class="account">
    <div class="head">
      <span class="title">Claude account</span>
      <span v-if="usage?.sample" class="dim">
        limits as of {{ age(usage.sample.age_s) }}, from the last SSSF agent call
      </span>
      <span v-else-if="usage" class="dim">no limit reading yet: one SSSF run on Claude Code records it</span>
      <span v-if="error" class="err">account unavailable: {{ error }}</span>
    </div>

    <div v-if="usage" class="meters">
      <div v-for="m in windows" :key="m.label" class="meter" :class="level(m.w)">
        <div class="meter-top">
          <span>{{ m.label }} window</span>
          <span class="pct">{{ m.w ? `${Math.round(m.w.utilization * 100)}%` : '—' }}</span>
        </div>
        <div class="bar"><div class="fill" :style="{ width: `${(m.w?.utilization ?? 0) * 100}%` }" /><div class="stop" /></div>
        <div class="faint small">
          {{ m.w ? `resets ${fmtDate(m.w.resets_at)}` : 'no live reading: the last one has reset' }}
        </div>
      </div>
      <div v-if="usage.sample?.status === 'rejected'" class="err">Claude rejected the last request: limit reached</div>
    </div>

    <div v-if="usage" class="spend">
      <div class="spend-head">
        <span>
          {{ usage.window.basis === 'five_hour' ? 'This 5-hour window' : 'Last 5 hours' }}:
          <b>{{ fmtCost(usage.window.est_usd) }}</b> est. over {{ usage.window.calls }} calls
        </span>
        <span class="dim">
          {{ usage.week.basis === 'seven_day' ? 'this 7-day window' : 'last 7 days' }}: {{ fmtCost(usage.week.est_usd) }}
        </span>
      </div>
      <div class="tables">
        <table v-for="t in [
          { name: 'by kind', rows: usage.window.by_kind, label: (k: string) => k },
          { name: 'by project', rows: usage.window.by_project, label: (k: string) => k },
          { name: 'by model', rows: usage.window.by_model, label: modelName },
        ]" :key="t.name">
          <thead><tr><th>{{ t.name }}</th><th>calls</th><th>est.</th><th></th><th>out</th></tr></thead>
          <tbody>
            <tr v-for="r in t.rows.slice(0, 8)" :key="r.key">
              <td class="key" :title="r.key">{{ t.label(r.key) }}</td>
              <td>{{ r.calls }}</td>
              <td>{{ fmtCost(r.est_usd) }}</td>
              <td class="dim">{{ share(r, usage.window.est_usd) }}</td>
              <td class="dim">{{ fmtTokens(r.output_tokens) }}</td>
            </tr>
            <tr v-if="!t.rows.length"><td colspan="5" class="dim">no calls in this window</td></tr>
          </tbody>
        </table>
      </div>
      <div class="faint small">
        Cost is estimated from transcript token counts at list price. The subscription pays it; it shows
        what fills the windows.
        <template v-if="usage.window.unpriced_models.length">
          No estimate for {{ usage.window.unpriced_models.join(', ') }}.
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.account {
  margin: 16px 24px 0;
  padding: 18px 22px;
  border: 1px solid var(--border-soft);
  border-radius: 16px;
  background: var(--surface);
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 14px;
}

.title {
  font-size: 18px;
  font-weight: 700;
}

.meters {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr));
  gap: 18px;
}

.meter-top {
  display: flex;
  justify-content: space-between;
  font-size: 15px;
}

.pct {
  font-family: var(--mono);
  font-weight: 700;
}

.bar {
  position: relative;
  height: 10px;
  margin: 6px 0;
  border-radius: 5px;
  background: var(--panel-2);
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 5px;
  background: var(--green);
}

/* The rate_limit_stop line: new SSSF agents stop at or past it. */
.stop {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 80%;
  width: 2px;
  background: var(--faint);
}

.mid .fill { background: var(--amber); }
.high .fill { background: var(--red); }
.high .pct { color: var(--red); }

.spend-head {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 10px;
  font-size: 15px;
}

.tables {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  align-items: start;
  gap: 18px;
  margin: 10px 0;
}

table {
  border-collapse: collapse;
  font-size: 13px;
  width: 100%;
}

th {
  text-align: left;
  font-weight: 400;
  color: var(--faint);
  padding: 0 8px 4px 0;
}

td {
  padding: 3px 8px 3px 0;
  font-family: var(--mono);
}

td.key {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--sans);
}

.small { font-size: 12px; }

/* At phone width the token column goes, so each table fits without scrolling. */
@media (max-width: 520px) {
  .account { margin: 12px 16px 0; padding: 14px 16px; }
  th:last-child, td:last-child { display: none; }
}
.err { color: var(--red); }
</style>
