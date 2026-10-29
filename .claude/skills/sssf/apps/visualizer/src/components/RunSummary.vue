<script setup lang="ts">
/**
 * Under the run strip: spend against the run's cap, why a failed run stopped,
 * and for an adw_tickets run one row per ticket. Each part draws only when the
 * run recorded what it needs, so an old run shows nothing here.
 */
import { computed } from 'vue'
import type { RunCaps, StopReason, TicketRun, TicketStatus } from '../lib/types'
import { fmtCost } from '../lib/format'

const props = defineProps<{
  cost: number | null
  caps: RunCaps | null
  stop: StopReason | null
  tickets: TicketRun | null
}>()
const emit = defineEmits<{ select: [phaseId: string] }>()

const cap = computed(() => props.caps?.max_run_cost ?? null)
const spentPct = computed(() => (cap.value ? Math.min(100, ((props.cost ?? 0) / cap.value) * 100) : 0))
const barLevel = computed(() => (spentPct.value >= 90 ? 'high' : spentPct.value >= 60 ? 'mid' : 'low'))

const STOP_LABELS: Record<StopReason['kind'], string> = {
  call_budget_stop: 'an agent call hit its max_call_cost',
  budget_stop: 'the run hit its max_run_cost',
  rate_limit_stop: 'the account neared its usage limit',
  not_accepted: 'the result was not accepted',
  phase_failed: 'a phase failed',
}

const STATUS_LABELS: Record<TicketStatus, string> = {
  done_before: 'done earlier',
  queued: 'queued',
  running: 'running',
  done: 'done',
  fail: 'failed',
  stopped: 'stopped',
}

function open(phaseId: string | null) {
  if (phaseId) emit('select', phaseId)
}
</script>

<template>
  <div v-if="cap || stop || tickets" class="run-summary">
    <div v-if="cap" class="budget" :title="`max_run_cost ${fmtCost(cap)}`">
      <div class="bar"><div class="fill" :class="barLevel" :style="{ width: `${spentPct}%` }" /></div>
      <span class="dim">{{ fmtCost(cost) }} of {{ fmtCost(cap) }} run cap</span>
    </div>

    <div v-if="stop" class="stop" :class="{ link: stop.phase_id }" @click="open(stop.phase_id)">
      <span class="stop-head">Stopped: {{ STOP_LABELS[stop.kind] }}</span>
      <pre class="stop-message">{{ stop.message }}</pre>
    </div>

    <table v-if="tickets" class="tickets">
      <caption>
        {{ tickets.feature }} <span class="dim">{{ tickets.folder }}</span>
      </caption>
      <thead>
        <tr><th>#</th><th>ticket</th><th>status</th><th class="num">fixes</th><th class="num">cost</th></tr>
      </thead>
      <tbody>
        <tr
          v-for="t in tickets.tickets"
          :key="t.number"
          :class="{ link: t.phase_id }"
          @click="open(t.phase_id)"
        >
          <td class="dim">{{ t.number }}</td>
          <td>{{ t.title }}</td>
          <td><span class="status" :class="t.status">{{ STATUS_LABELS[t.status] }}</span></td>
          <td class="num" :class="{ dim: !t.retries }">{{ t.retries }}</td>
          <td class="num" :class="{ dim: !t.cost }">{{ t.cost ? fmtCost(t.cost) : '–' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.run-summary {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 24px 14px;
  border-bottom: 1px solid var(--border-soft);
}

.dim {
  color: var(--dim);
}

.budget {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 15px;
}

.bar {
  width: 240px;
  max-width: 50%;
  height: 8px;
  border-radius: 999px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  overflow: hidden;
}

.fill {
  height: 100%;
}
.fill.low {
  background: var(--green);
}
.fill.mid {
  background: var(--amber);
}
.fill.high {
  background: var(--red);
}

.stop {
  border: 1px solid rgba(255, 111, 103, 0.45);
  background: rgba(255, 111, 103, 0.07);
  border-radius: 8px;
  padding: 10px 14px;
}

.stop-head {
  color: var(--red);
  font-size: 15px;
}

.stop-message {
  margin: 6px 0 0;
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.link {
  cursor: pointer;
}

.tickets {
  border-collapse: collapse;
  font-size: 15px;
  max-width: 820px;
}

.tickets caption {
  text-align: left;
  padding-bottom: 6px;
  color: var(--text);
}

.tickets th {
  text-align: left;
  font-weight: normal;
  color: var(--faint);
  padding: 4px 14px 4px 0;
  border-bottom: 1px solid var(--border-soft);
}

.tickets td {
  padding: 5px 14px 5px 0;
  border-bottom: 1px solid var(--border-soft);
}

.tickets tr.link:hover td {
  background: var(--panel-2);
}

.tickets .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.status {
  color: var(--dim);
}
.status.done,
.status.done_before {
  color: var(--green);
}
.status.done_before {
  opacity: 0.6;
}
.status.running {
  color: var(--blue);
}
.status.fail,
.status.stopped {
  color: var(--red);
}
</style>
