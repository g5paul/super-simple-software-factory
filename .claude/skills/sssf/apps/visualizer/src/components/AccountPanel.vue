<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { AccountUsage, LimitWindow, SpanSpend } from '../lib/types'
import { isSpan, SPAN_MS, type Span } from '@shared/spans'
import { fetchAccount } from '../lib/api'
import { fmtCost, fmtDate } from '../lib/format'
import { modelName } from '../lib/models'
import MachineGauge from './MachineGauge.vue'
import SandboxGauge from './SandboxGauge.vue'
import SpendTables from './SpendTables.vue'
import SpanPicker from './SpanPicker.vue'

const usage = shallowRef<AccountUsage | null>(null)
const error = ref<string | null>(null)

// Each card's span, remembered in this browser only.
function remembered(key: string): Span {
  try {
    const saved = localStorage.getItem(key)
    return isSpan(saved) ? saved : '5h'
  } catch {
    return '5h'
  }
}
const claudeSpan = ref<Span>(remembered('sssf.account.span'))
const piSpan = ref<Span>(remembered('sssf.account.pi_span'))
watch([claudeSpan, piSpan], ([c, p]) => {
  try {
    localStorage.setItem('sssf.account.span', c)
    localStorage.setItem('sssf.account.pi_span', p)
  } catch {
    // a blocked store just forgets the choice
  }
  void tick()
})

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false
let again = false

// Slow on purpose: each tick reads whatever the transcripts appended. A span
// picked while a fetch runs asks again once it ends, so no pick is lost.
async function tick() {
  if (inflight) {
    again = true
    return
  }
  inflight = true
  try {
    do {
      again = false
      const got = await fetchAccount(claudeSpan.value, piSpan.value)
      if (!again) usage.value = got
      error.value = null
    } while (again)
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

// A model in none of the lists has a price.
const unlisted = computed(() =>
  [...new Set([...(usage.value?.span.unpriced_models ?? []), ...(usage.value?.week.unpriced_models ?? [])])].toSorted())

const NAMES: Record<Span, string> = {
  '5h': '5 hours', '24h': '24 hours', '7d': '7 days', '30d': '30 days', '60d': '60 days', '90d': '90 days',
}

function spanLabel(s: SpanSpend): string {
  return s.basis === 'five_hour' ? 'This 5-hour window' : `Last ${NAMES[s.span]}`
}

function spentWhere(s: SpanSpend): string {
  return s.basis === 'five_hour' ? "Where this 5-hour window's spend went" : `Where the last ${NAMES[s.span]}' spend went`
}

// Older transcripts are gone, so the span starts before the data does.
function coverage(s: SpanSpend): string {
  if (!s.data_from) return ''
  const from = Date.parse(s.data_from)
  const days = Math.max(1, Math.round((Date.parse(s.end) - from) / 86_400_000))
  const start = `Claude Code's transcripts start ${new Date(s.data_from).toLocaleDateString([], { month: 'short', day: 'numeric' })}`
  return SPAN_MS[s.span] >= 86_400_000
    ? `Covers ${days} of ${Math.round(SPAN_MS[s.span] / 86_400_000)} days. ${start}.`
    : `${start}.`
}

function stretch(p: { start: string, end: string }): string {
  const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })
  const hm = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  const across = day(p.start) === day(p.end) ? '' : `${day(p.end)} `
  return `${day(p.start)} ${hm(p.start)} to ${across}${hm(p.end)}`
}

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
        <div class="spend-bar">
          <div class="totals">
            <span>
              {{ spanLabel(usage.span) }}:
              <b>{{ fmtCost(usage.span.est_usd) }}</b> est. over {{ usage.span.calls }} calls
            </span>
            <span>
              {{ usage.week.basis === 'seven_day' ? 'This 7-day window' : 'Last 7 days' }}:
              <b>{{ fmtCost(usage.week.est_usd) }}</b> est.
            </span>
          </div>
          <SpanPicker v-model="claudeSpan" />
        </div>
        <div v-if="coverage(usage.span)" class="coverage">{{ coverage(usage.span) }}</div>
        <div v-if="usage.span.peaks.length" class="peaks">
          <div class="caption">Busiest 5-hour stretches, what a 5-hour limit would have seen</div>
          <ol>
            <li v-for="p in usage.span.peaks" :key="p.start">
              <b>{{ fmtCost(p.est_usd) }}</b> over {{ p.calls }} calls, {{ stretch(p) }}
            </li>
          </ol>
        </div>
        <SpendTables
          :caption="spentWhere(usage.span)"
          :total="usage.span.est_usd"
          empty="no calls in this span"
          :tables="[
            { name: 'by kind', rows: usage.span.by_kind },
            { name: 'by project', rows: usage.span.by_project },
            { name: 'by model', rows: usage.span.by_model, label: modelName },
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

    <div v-if="usage?.pi.limit_hit || usage?.pi.week.calls || usage?.pi.span.calls" class="meter-card">
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

      <template v-if="usage.pi.week.calls || usage.pi.span.calls">
        <div class="spend-bar">
          <div class="totals">
            <span>{{ spanLabel(usage.pi.span) }}: <b>{{ fmtCost(usage.pi.span.est_usd) }}</b> est. over {{ usage.pi.span.calls }} calls</span>
            <span>Last 7 days: <b>{{ fmtCost(usage.pi.week.est_usd) }}</b> est.</span>
          </div>
          <SpanPicker v-model="piSpan" />
        </div>
        <div v-if="usage.pi.span.peaks.length" class="peaks">
          <div class="caption">Busiest 5-hour stretches</div>
          <ol>
            <li v-for="p in usage.pi.span.peaks" :key="p.start">
              <b>{{ fmtCost(p.est_usd) }}</b> over {{ p.calls }} calls, {{ stretch(p) }}
            </li>
          </ol>
        </div>
        <SpendTables
          :caption="spentWhere(usage.pi.span)"
          :total="usage.pi.span.est_usd"
          :empty="`no pi calls in the last ${NAMES[usage.pi.span.span]}`"
          :tables="[
            { name: 'by project', rows: usage.pi.span.by_project },
            { name: 'by model', rows: usage.pi.span.by_model },
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

.spend-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px 20px;
}

.coverage {
  font-size: 13px;
  color: var(--amber);
}

.caption {
  font-size: 13px;
  color: var(--faint);
  margin-bottom: 4px;
}

.peaks ol {
  margin: 0;
  padding-left: 22px;
  font-size: 14px;
  display: flex;
  flex-direction: column;
  gap: 2px;
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
