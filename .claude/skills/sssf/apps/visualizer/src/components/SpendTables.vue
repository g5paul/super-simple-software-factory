<script setup lang="ts">
import type { SpendRow } from '../lib/types'
import { fmtCost, fmtTokens } from '../lib/format'

// Where one window's spend went, a table per breakdown, side by side.
defineProps<{
  caption: string
  total: number
  empty: string
  tables: { name: string, rows: SpendRow[], label?: (key: string) => string }[]
}>()

function share(row: SpendRow, total: number): string {
  return total > 0 ? `${Math.round((row.est_usd / total) * 100)}%` : ''
}

const SHOWN = 8

/** The rows added up into one, under the given key. */
function addUp(rows: SpendRow[], key: string): SpendRow {
  const out: SpendRow = { key, calls: 0, est_usd: 0, output_tokens: 0, input_tokens: 0 }
  for (const r of rows) {
    out.calls += r.calls
    out.est_usd += r.est_usd
    out.output_tokens += r.output_tokens
    out.input_tokens += r.input_tokens
  }
  return out
}

// The top rows, and the rest folded into one, so every table adds up to the total.
function shown(rows: SpendRow[]): SpendRow[] {
  if (rows.length <= SHOWN) return rows
  const rest = rows.slice(SHOWN - 1)
  return [...rows.slice(0, SHOWN - 1), addUp(rest, `${rest.length} others`)]
}
</script>

<template>
  <div class="spend-tables">
    <div class="caption">{{ caption }}</div>
    <div class="tables">
      <table v-for="t in tables" :key="t.name">
        <thead><tr><th>{{ t.name }}</th><th>calls</th><th>est.</th><th></th><th>out</th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in shown(t.rows)" :key="r.key" :class="{ others: t.rows.length > SHOWN && i === SHOWN - 1 }">
            <td class="key" :title="r.key">{{ t.label && !(t.rows.length > SHOWN && i === SHOWN - 1) ? t.label(r.key) : r.key }}</td>
            <td>{{ r.calls }}</td>
            <td>{{ fmtCost(r.est_usd) }}</td>
            <td class="dim">{{ share(r, total) }}</td>
            <td class="dim">{{ fmtTokens(r.output_tokens) }}</td>
          </tr>
          <tr v-if="!t.rows.length"><td colspan="5" class="dim">{{ empty }}</td></tr>
        </tbody>
        <tfoot v-if="t.rows.length > 1">
          <tr>
            <td class="key">total</td>
            <td>{{ addUp(t.rows, 'total').calls }}</td>
            <td>{{ fmtCost(addUp(t.rows, 'total').est_usd) }}</td>
            <td></td>
            <td class="dim">{{ fmtTokens(addUp(t.rows, 'total').output_tokens) }}</td>
          </tr>
        </tfoot>
      </table>
    </div>
  </div>
</template>

<style scoped>
.caption {
  font-size: 14px;
  color: var(--dim);
  margin-bottom: 6px;
}

.tables {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr));
  align-items: start;
  gap: 14px;
}

table {
  border-collapse: collapse;
  font-size: 13px;
  width: 100%;
  border: 1px solid var(--border-soft);
  border-radius: 8px;
  overflow: hidden;
}

thead tr { background: var(--panel-2); }

th {
  text-align: left;
  font-weight: 600;
  color: var(--dim);
  padding: 6px 8px;
}

td {
  padding: 4px 8px;
  font-family: var(--mono);
  border-top: 1px solid var(--border-soft);
}

tr.others td { color: var(--dim); font-style: italic; }

tfoot td {
  border-top: 1px solid var(--border);
  font-weight: 700;
}

td.key {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--sans);
}

/* At phone width the token column goes, so each table fits without scrolling. */
@media (max-width: 520px) {
  th:last-child, td:last-child:not([colspan]) { display: none; }
}
</style>
