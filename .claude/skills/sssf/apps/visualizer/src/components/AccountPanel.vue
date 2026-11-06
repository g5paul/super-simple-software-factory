<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef } from 'vue'
import type { AccountUsage, LimitWindow } from '../lib/types'
import { fetchAccount } from '../lib/api'
import { fmtCost, fmtDate } from '../lib/format'
import { modelName } from '../lib/models'
import MachineGauge from './MachineGauge.vue'
import SandboxGauge from './SandboxGauge.vue'
import SpendTables from './SpendTables.vue'

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

// The week's list holds the window's; a model in neither has a price.
const unlisted = computed(() =>
  [...new Set([...(usage.value?.window.unpriced_models ?? []), ...(usage.value?.week.unpriced_models ?? [])])].toSorted())

const windows = computed(() => {
  const s = usage.value?.sample
  // Plans below Ultra have a 5-hour window; with no sample yet, show both.
  return [
    ...(s && !s.has_five_hour ? [] : [{ label: '5-hour', w: s?.five_hour ?? null }]),
    { label: '7-day', w: s?.seven_day ?? null },
  ]
})
</script>

<template>
  <section class="account">
    <div v-if="error" class="err">account unavailable: {{ error }}</div>

    <div class="meter-card">
      <div class="card-head">
        <span class="card-title">Claude</span>
        <span v-if="usage?.sample" class="dim">
          limits as of {{ age(usage.sample.age_s) }}, from the last SSSF agent call
        </span>
        <span v-else-if="usage" class="dim">no limit reading yet: one SSSF run on Claude Code records it</span>
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
        <div class="totals">
          <span>
            {{ usage.window.basis === 'five_hour' ? 'This 5-hour window' : 'Last 5 hours' }}:
            <b>{{ fmtCost(usage.window.est_usd) }}</b> est. over {{ usage.window.calls }} calls
          </span>
          <span>
            {{ usage.week.basis === 'seven_day' ? 'This 7-day window' : 'Last 7 days' }}:
            <b>{{ fmtCost(usage.week.est_usd) }}</b> est.
          </span>
        </div>
        <SpendTables
          :caption="usage.window.basis === 'five_hour' ? 'Where this 5-hour window\'s spend went' : 'Where the last 5 hours\' spend went'"
          :total="usage.window.est_usd"
          empty="no calls in this window"
          :tables="[
            { name: 'by kind', rows: usage.window.by_kind },
            { name: 'by project', rows: usage.window.by_project },
            { name: 'by model', rows: usage.window.by_model, label: modelName },
          ]"
        />
        <div class="faint small">
          Cost is estimated from transcript token counts at list price. The subscription pays it; it shows
          what fills the windows.
        </div>
        <div v-if="unlisted.length" class="unlisted">
          No list price for {{ unlisted.join(', ') }}, so its calls are left out of these estimates.
          Add it to the skill's <code>adws/adw_modules/prices.json</code> from
          <a href="https://claude.com/pricing" target="_blank" rel="noopener">claude.com/pricing</a>.
        </div>
      </div>
    </div>

    <div v-if="usage?.pi.limit_hit || usage?.pi.week.calls" class="meter-card">
      <div class="card-head">
        <span class="card-title">pi agents</span>
        <span class="dim">ChatGPT and the other pi providers</span>
      </div>

      <div v-if="usage.pi.limit_hit" class="pi-limit">
        The ChatGPT plan was out of usage at {{ fmtDate(usage.pi.limit_hit.at) }} (run
        <a :href="`#/${encodeURIComponent(usage.pi.limit_hit.project)}/${encodeURIComponent(usage.pi.limit_hit.adw_id)}`">{{ usage.pi.limit_hit.adw_id }}</a>,
        {{ usage.pi.limit_hit.project }}). OpenAI gives no reset time, so it may have usage again:
        <a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener">check its usage</a>.
      </div>

      <template v-if="usage.pi.week.calls">
        <div class="totals">
          <span>Last 5 hours: <b>{{ fmtCost(usage.pi.window.est_usd) }}</b> est. over {{ usage.pi.window.calls }} calls</span>
          <span>Last 7 days: <b>{{ fmtCost(usage.pi.week.est_usd) }}</b> est.</span>
        </div>
        <SpendTables
          caption="Where the last 5 hours' spend went"
          :total="usage.pi.window.est_usd"
          empty="no pi calls in the last 5 hours"
          :tables="[
            { name: 'by project', rows: usage.pi.window.by_project },
            { name: 'by model', rows: usage.pi.window.by_model },
          ]"
        />
        <div class="faint small">
          Cost is pi's list-price figure. A subscription sign-in, such as ChatGPT, pays it. pi's providers
          report no usage limits to SSSF, so check a ChatGPT plan's limits in ChatGPT or Codex. A run
          that reaches the plan's limit stops there, and shows here for 5 hours.
        </div>
      </template>
    </div>

    <div class="side-by-side">
      <SandboxGauge />
      <MachineGauge />
    </div>
  </section>
</template>

<style scoped>
.account {
  margin: 16px 24px 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Sandboxes and the machine cap share a row on a wide screen; either may be absent. */
.side-by-side {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(360px, 100%), 1fr));
  gap: 16px;
}

.side-by-side:not(:has(*)) { display: none; }

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

/* The rule between the limits and the spend. */
.meters + .spend {
  padding-top: 12px;
  border-top: 1px solid var(--border-soft);
}

.spend {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.totals {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 28px;
  font-size: 15px;
}

.unlisted { color: var(--amber); }

.unlisted a,
.pi-limit a {
  color: inherit;
  text-decoration: underline;
}

.pi-limit {
  font-size: 15px;
  color: var(--red);
}

.small { font-size: 12px; }

@media (max-width: 520px) {
  .account { margin: 12px 16px 0; }
}

.err { color: var(--red); }
</style>
