<script setup lang="ts">
import { computed, onMounted, onUnmounted, shallowRef } from 'vue'
import type { TrialAttemptCard, TrialView } from '../lib/types'
import { fetchTrial } from '../lib/api'
import { hrefFor } from '../lib/router'
import { rosterLabel } from '../lib/format'

// One trial's attempts side by side (trials.md decision 24): the gates, cost,
// time, diff, and the panel's ranks, with the suggestion trial.py recorded.
// Before a panel has run, the rows are the attempts' runs as they stand.
const props = defineProps<{ trial: string }>()

const view = shallowRef<TrialView | null>(null)
const error = shallowRef<string | null>(null)
let timer: ReturnType<typeof setInterval> | undefined

async function tick() {
  try {
    view.value = await fetchTrial(props.trial)
    error.value = null
  } catch (e) {
    error.value = String(e)
  }
}

onMounted(() => {
  void tick()
  timer = setInterval(() => void tick(), 5_000)
})
onUnmounted(() => clearInterval(timer))

const dollars = (n: number) => `$${n.toFixed(2)}`
function duration(s: number | null): string {
  if (s === null) return '-'
  const m = Math.floor(s / 60)
  return `${m}m${String(Math.floor(s % 60)).padStart(2, '0')}s`
}
const checks = (c: boolean | null) => (c === null ? '-' : c ? 'pass' : 'fail')
const diff = (a: TrialAttemptCard) => (a.added === undefined ? '-' : `+${a.added} -${a.removed}`)
const rank = (r: number | null) => (r === null ? '-' : String(r))
const blocks = computed(() =>
  (view.value?.attempts ?? []).flatMap((a) =>
    a.rulings
      .filter((r) => !r.approved)
      .map((r) => ({ adw_id: a.adw_id, family: r.family, reasons: [...r.blocking, ...r.unmet.map((u) => `unmet: ${u}`)] })),
  ),
)
const oneFamily = computed(() => (view.value?.families.length ?? 0) < 2)
</script>

<template>
  <div class="trial">
    <div v-if="error && !view" class="dim">{{ error }}</div>
    <template v-if="view">
      <h2>trial {{ view.trial }}</h2>
      <p class="request">{{ view.request }}</p>
      <p class="dim">
        {{ view.attempts.length }} attempts from <code>{{ view.base.slice(0, 7) }}</code>
        <template v-if="view.panel">
          · panel <a :href="hrefFor(view.panel)">{{ view.panel }}</a> ({{ view.families.join(', ') }})
        </template>
      </p>
      <table>
        <thead>
          <tr>
            <th>attempt</th><th>roster</th><th>run</th><th>checks</th><th>panel</th>
            <th title="average place across the panel, 1 is best">rank</th>
            <th title="leaves out the vote of the reviewer from the attempt's own family">rank*</th>
            <th>cost</th><th>time</th><th>diff</th><th>tokens</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="a in view.attempts"
            :key="a.adw_id"
            :class="{ suggested: a.adw_id === view.suggested, merged: a.adw_id === view.merged }"
          >
            <td><a :href="hrefFor(a.adw_id)">{{ a.adw_id }}</a></td>
            <td>{{ rosterLabel(a.roster, a.coding_agent) }}</td>
            <td :class="a.status">{{ a.live ? 'live' : a.status ?? '?' }}</td>
            <td :class="checks(a.checks)">{{ checks(a.checks) }}</td>
            <td :class="a.review ?? ''">{{ a.review ?? '-' }}</td>
            <td>{{ rank(a.rank) }}</td>
            <td>{{ rank(a.rank_others) }}</td>
            <td>{{ dollars(a.cost) }}</td>
            <td>{{ duration(a.seconds) }}</td>
            <td>{{ diff(a) }}</td>
            <td>{{ a.tokens.toLocaleString() }}</td>
          </tr>
        </tbody>
      </table>
      <ul v-if="blocks.length" class="blocks">
        <li v-for="b in blocks" :key="`${b.adw_id}-${b.family}`">
          <b>{{ b.adw_id }}</b> blocked by the {{ b.family }} reviewer: {{ b.reasons.join('; ') }}
        </li>
      </ul>
      <p v-if="view.scored && oneFamily" class="dim">With one family on the panel, rank* is empty.</p>
      <p v-if="view.merged" class="outcome merged">merged {{ view.merged }}</p>
      <p v-else class="outcome">
        <template v-if="view.suggested">suggested: </template>{{ view.why }}
      </p>
      <p v-if="view.panel && view.panel_status === 'fail'" class="dim">
        The panel run did not finish; <code>just trial-review {{ view.trial }}</code> runs it again.
      </p>
      <p class="dim">
        total {{ dollars(view.total_cost) }}, every attempt plus the panel ({{ dollars(view.panel_cost) }})
      </p>
    </template>
  </div>
</template>

<style scoped>
.trial {
  padding: 20px 28px;
  max-width: 1100px;
}

h2 {
  margin: 0 0 6px;
  font-size: 20px;
}

.request {
  white-space: pre-wrap;
  margin: 0 0 6px;
}

table {
  border-collapse: collapse;
  margin: 14px 0;
  font-size: 15px;
  width: 100%;
}

th,
td {
  text-align: left;
  padding: 6px 12px 6px 0;
  border-bottom: 1px solid var(--border-soft);
  white-space: nowrap;
}

th {
  color: var(--faint);
  font-weight: normal;
}

td a {
  color: inherit;
  text-decoration: underline;
}

tr.suggested td:first-child::after {
  content: ' suggested';
  color: var(--green);
  font-size: 13px;
}

tr.merged td:first-child::after {
  content: ' merged';
  color: var(--cyan);
  font-size: 13px;
}

.success,
.pass { color: var(--green); }
.fail,
.block { color: var(--red); }
.unknown,
.running { color: var(--amber); }

.blocks {
  padding-left: 18px;
  color: var(--dim);
}

.outcome {
  font-size: 16px;
}

.outcome.merged {
  color: var(--cyan);
}
</style>
