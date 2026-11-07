<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { SandboxAllowance, SandboxSpan, SandboxUsage } from '../lib/types'
import { fetchSandboxUsage } from '../lib/api'
import { isSpan, type Span } from '@shared/spans'
import SpanPicker from './SpanPicker.vue'

// What remote VM runs in every registered repo used of each provider's free
// allowance: Vercel Hobby's monthly CPU, memory and creations, and E2B's
// one-time credit. The same numbers as `just sandbox-usage`. One line per
// provider, its fullest allowance as the bar and the rest on hover; the
// picker's span sets the runs line under it, and the bar keeps the
// allowance's own window. In the account panel, only once a repo has run in a VM.
const usage = shallowRef<SandboxUsage | null>(null)
const SPAN_KEY = 'sssf.account.sandbox_span'
const span = ref<Span>(stored())
let timer: ReturnType<typeof setInterval> | undefined
let inflight = false
let again = false

function stored(): Span {
  try {
    const s = localStorage.getItem(SPAN_KEY)
    return isSpan(s) ? s : '30d'
  } catch {
    return '30d'
  }
}

async function tick() {
  if (inflight) {
    again = true
    return
  }
  inflight = true
  try {
    do {
      again = false
      usage.value = await fetchSandboxUsage(span.value)
    } while (again)
  } catch {
    // keep the last reading; the panel's own poll shows the API is down
  } finally {
    inflight = false
  }
}

watch(span, (s) => {
  try {
    localStorage.setItem(SPAN_KEY, s)
  } catch {
    // the pick holds for this page only
  }
  void tick()
})

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 30_000)
})

onUnmounted(() => clearInterval(timer))

interface Bar {
  label: string
  used: string
  share: number
}

function bars(p: SandboxAllowance): Bar[] {
  if (p.provider === 'vercel') {
    const cpu = p.cpu_seconds / 3600
    const cpuKnown = p.cpu_unknown < p.runs
    return [
      { label: 'active CPU', used: cpuKnown ? `${cpu.toFixed(3)} of ${p.allowed.cpu_hours} h` : `unknown of ${p.allowed.cpu_hours} h`, share: cpu / (p.allowed.cpu_hours ?? 1) },
      { label: 'memory', used: `${p.gb_hours.toFixed(2)} of ${p.allowed.gb_hours} GB-h`, share: p.gb_hours / (p.allowed.gb_hours ?? 1) },
      { label: 'creations', used: `${p.runs} of ${p.allowed.creations}`, share: p.runs / (p.allowed.creations ?? 1) },
    ]
  }
  return [{ label: 'credit', used: `$${p.cost_usd.toFixed(4)} of $${p.allowed.credit_usd}`, share: p.cost_usd / (p.allowed.credit_usd ?? 1) }]
}

/** The allowance nearest its limit stands for the provider on the line. */
function fullest(p: SandboxAllowance): Bar {
  return bars(p).reduce((a, b) => (b.share > a.share ? b : a))
}

function level(share: number): string {
  return share >= 1 ? 'high' : share >= 0.8 ? 'mid' : 'low'
}

function name(p: { provider: string }): string {
  return p.provider === 'vercel' ? 'Vercel' : 'E2B'
}

function basis(p: SandboxAllowance): string {
  return p.window_days ? `${p.window_days}-day allowance` : 'one-time credit'
}

function pct(share: number): string {
  return share > 0 && share < 0.01 ? 'under 1%' : `${Math.round(share * 100)}%`
}

function perRun(p: SandboxAllowance): string {
  const measured = p.runs - p.cpu_unknown
  if (p.provider === 'vercel' && measured > 0 && p.cpu_seconds > 0) {
    const each = p.cpu_seconds / measured
    return `about ${each.toFixed(0)} CPU seconds a run, so 5 hours covers about ${Math.round(((p.allowed.cpu_hours ?? 0) * 3600) / each)} runs a month`
  }
  if (p.provider === 'e2b' && p.cost_usd > 0) {
    const each = p.cost_usd / p.runs
    return `about $${each.toFixed(4)} a run, so what's left covers about ${Math.round(((p.allowed.credit_usd ?? 0) - p.cost_usd) / each)} runs`
  }
  return ''
}

/** Everything the line leaves out, for its hover. */
function details(p: SandboxAllowance): string {
  const when = p.window_days ? `last ${p.window_days} days` : 'all time'
  return [
    `${name(p)}, ${when}: ${p.runs} ${p.runs === 1 ? 'run' : 'runs'}`,
    ...bars(p).map((b) => `${b.label}: ${b.used}`),
    perRun(p),
    p.cpu_unknown ? `${p.cpu_unknown} of ${p.runs} runs have no CPU reading` : '',
    p.repos.length ? `by repo: ${p.repos.map((r) => `${r.name} ${r.runs}`).join(', ')}` : '',
  ].filter(Boolean).join('\n')
}

const NAMES: Record<Span, string> = { '5h': '5 hours', '24h': '24 hours', '7d': '7 days', '30d': '30 days', '60d': '60 days', '90d': '90 days' }

function spent(t: SandboxSpan): string {
  const minutes = t.vm_seconds / 60
  const used = t.provider === 'vercel' ? `${t.gb_hours.toFixed(2)} GB-h` : `$${t.cost_usd.toFixed(4)}`
  return `${t.runs} ${t.runs === 1 ? 'run' : 'runs'}, ${minutes.toFixed(1)} VM min, ${Math.round(t.cpu_seconds)} CPU s, ${used}`
}

const providers = computed(() => usage.value?.providers ?? [])
const inSpan = (p: SandboxAllowance) => usage.value?.in_span.find((t) => t.provider === p.provider)
const tip = "SSSF's runs in every registered repo, against each provider's Hobby allowance from its pricing page. Built in, not a setting. The provider's dashboard is the bill."
</script>

<template>
  <div v-if="providers.length" class="meter-card sandbox">
    <div class="card-head">
      <span class="card-title">Sandboxes</span>
      <span class="dim" :title="tip">VM runs in every repo, against each provider's free allowance</span>
    </div>
    <div class="spend-bar">
      <span class="dim small">Runs over the last {{ NAMES[span] }}; hover a line for the rest</span>
      <SpanPicker v-model="span" />
    </div>
    <div v-for="p in providers" :key="p.provider" class="provider" :title="details(p)">
      <div class="row" :class="level(fullest(p).share)">
        <span class="label">{{ name(p) }}</span>
        <div class="bar"><div class="fill" :style="{ width: `${Math.min(fullest(p).share, 1) * 100}%` }" /></div>
        <span class="used">{{ pct(fullest(p).share) }} of the {{ basis(p) }}{{ bars(p).length > 1 ? `, ${fullest(p).label}` : '' }}</span>
      </div>
      <div class="note dim">
        {{ inSpan(p) ? spent(inSpan(p)!) : 'no runs' }} in the last {{ NAMES[span] }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.sandbox { font-size: 15px; }

.spend-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 8px 16px;
}

.small { font-size: 13px; }

.provider { cursor: help; }

.provider + .provider {
  padding-top: 10px;
  border-top: 1px solid var(--border-soft);
}

.row {
  display: grid;
  grid-template-columns: 56px minmax(60px, 1fr) auto;
  align-items: center;
  gap: 10px;
  font-size: 14px;
}

.label { font-weight: 600; }

.bar {
  height: 8px;
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
.high .used { color: var(--red); }

.note {
  margin-top: 4px;
  font-size: 13px;
}

@media (max-width: 520px) {
  .row { grid-template-columns: 56px 1fr; }
  .used { grid-column: 1 / -1; }
}
</style>
