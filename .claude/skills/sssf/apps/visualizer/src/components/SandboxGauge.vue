<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from 'vue'
import type { SandboxAllowance, SandboxUsage } from '../lib/types'
import { fetchSandboxUsage } from '../lib/api'

// What remote VM runs in every registered repo used of each provider's free
// allowance: Vercel Hobby's monthly CPU, memory and creations, and E2B's
// one-time credit. The same numbers as `just sandbox-usage`. In the account
// panel, only once a repo has run in a VM.
const usage = shallowRef<SandboxUsage | null>(null)
let timer: ReturnType<typeof setInterval> | undefined
let inflight = false

async function tick() {
  if (inflight) return
  inflight = true
  try {
    usage.value = await fetchSandboxUsage()
  } catch {
    // keep the last reading; the panel's own poll shows the API is down
  } finally {
    inflight = false
  }
}

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

function level(share: number): string {
  return share >= 1 ? 'high' : share >= 0.8 ? 'mid' : 'low'
}

function heading(p: SandboxAllowance): string {
  const when = p.window_days ? `last ${p.window_days} days` : 'all time'
  return `${p.provider === 'vercel' ? 'Vercel' : 'E2B'} sandbox, ${when}: ${p.runs} ${p.runs === 1 ? 'run' : 'runs'}`
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

const providers = computed(() => usage.value?.providers ?? [])
const tip = "SSSF's runs in every registered repo, against each provider's Hobby allowance from its pricing page. Built in, not a setting. The provider's dashboard is the bill."
</script>

<template>
  <div v-if="providers.length" class="sandbox" :title="tip">
    <div v-for="p in providers" :key="p.provider" class="provider">
      <div class="line">{{ heading(p) }}</div>
      <div v-for="b in bars(p)" :key="b.label" class="row" :class="level(b.share)">
        <span class="label dim">{{ b.label }}</span>
        <div class="bar"><div class="fill" :style="{ width: `${Math.min(b.share, 1) * 100}%` }" /></div>
        <span class="used">{{ b.used }}</span>
      </div>
      <div v-if="perRun(p)" class="note dim">{{ perRun(p) }}</div>
      <div v-if="p.cpu_unknown" class="note dim">{{ p.cpu_unknown }} of {{ p.runs }} runs have no CPU reading</div>
      <div class="repos dim">
        <span v-for="r in p.repos" :key="r.name">{{ r.name }} {{ r.runs }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sandbox {
  font-size: 15px;
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.row {
  display: grid;
  grid-template-columns: 84px minmax(60px, 1fr) auto;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  font-size: 14px;
}

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

.note,
.repos {
  margin-top: 4px;
  font-size: 14px;
}

.repos {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
}
</style>
