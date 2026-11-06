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
</script>

<template>
  <div class="spend-tables">
    <div class="caption">{{ caption }}</div>
    <div class="tables">
      <table v-for="t in tables" :key="t.name">
        <thead><tr><th>{{ t.name }}</th><th>calls</th><th>est.</th><th></th><th>out</th></tr></thead>
        <tbody>
          <tr v-for="r in t.rows.slice(0, 8)" :key="r.key">
            <td class="key" :title="r.key">{{ t.label ? t.label(r.key) : r.key }}</td>
            <td>{{ r.calls }}</td>
            <td>{{ fmtCost(r.est_usd) }}</td>
            <td class="dim">{{ share(r, total) }}</td>
            <td class="dim">{{ fmtTokens(r.output_tokens) }}</td>
          </tr>
          <tr v-if="!t.rows.length"><td colspan="5" class="dim">{{ empty }}</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.caption {
  font-size: 13px;
  color: var(--faint);
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
