<script setup lang="ts">
import { onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { FactoryBoard, FactoryRow } from '../lib/types'
import { fetchFactory } from '../lib/api'
import { projectHref } from '../lib/router'

const KEY = 'sssf.factory.listed'
const board = shallowRef<FactoryBoard | null>(null)
const apiError = ref<string | null>(null)
const listed = ref(localStorage.getItem(KEY) === '1')

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false
const previousTitle = document.title

async function load(refresh = false) {
  if (inflight) return
  inflight = true
  try {
    board.value = await fetchFactory(listed.value, refresh)
    apiError.value = null
  } catch (err) {
    apiError.value = err instanceof Error ? err.message : String(err)
  } finally {
    inflight = false
  }
}

watch(
  board,
  (b) => {
    document.title = b && b.red > 0 ? `(${b.red}) SSSF factory` : 'SSSF factory'
  },
  { immediate: true },
)

watch(listed, (on) => {
  localStorage.setItem(KEY, on ? '1' : '0')
  void load()
})

onMounted(() => {
  void load()
  timer = setInterval(() => void load(), 10_000)
})

onUnmounted(() => {
  clearInterval(timer)
  document.title = previousTitle
})

function titleHref(row: FactoryRow): string | null {
  if (!row.repo) return null
  const base = projectHref(row.repo)
  return row.adw_id ? `${base}/${encodeURIComponent(row.adw_id)}` : base
}

function isCommand(next: string): boolean {
  return next.includes('`') || /^(sudo|just|uv|bun) /.test(next)
}

function age(seconds: number | null): string {
  if (seconds === null) return '?'
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}
</script>

<template>
  <section class="factory">
    <div v-if="apiError" class="error-bar">factory board: {{ apiError }}</div>

    <div class="strip">
      <template v-if="board">
        <span class="count red">{{ board.red }} red</span>
        <span class="count amber">{{ board.amber }} amber</span>
        <span class="dim">at {{ board.generated_at }}</span>
        <span v-if="board.scripts_at" class="dim">scripts at {{ board.scripts_at }}</span>
      </template>
      <span v-else class="dim">reading the factory…</span>
      <span class="spacer" />
      <label
        class="switch"
        :title="board && !board.private_names ? 'the hub has no .git/sssf-private-names' : ''"
      >
        <input
          v-model="listed"
          type="checkbox"
          :disabled="board !== null && !board.private_names"
        />
        listed only
      </label>
      <span v-if="board && listed && board.private_names" class="dim">
        {{ board.hidden }} unlisted repos hidden
      </span>
      <button type="button" @click="load(true)">refresh now</button>
    </div>

    <ul v-if="board && board.errors.length" class="errors">
      <li v-for="e in board.errors" :key="e">{{ e }}</li>
    </ul>

    <ul v-if="board" class="rows">
      <li v-for="row in board.rows" :key="row.key" class="row" :class="row.level">
        <span class="dot" :class="row.level" :title="row.level" />
        <div class="body">
          <div class="head">
            <span class="chip">{{ row.section }}</span>
            <a v-if="titleHref(row)" :href="titleHref(row)!" class="title">{{ row.title }}</a>
            <span v-else class="title">{{ row.title }}</span>
            <span v-if="row.figure" class="figure">{{ row.figure }}</span>
          </div>
          <div v-for="p in row.problems" :key="p" class="problem">{{ p }}</div>
          <div v-if="row.next" class="next" :class="{ cmd: isCommand(row.next) }">
            next: {{ row.next }}
          </div>
          <div v-if="row.notes.length" class="notes dim">{{ row.notes.join(' · ') }}</div>
          <div
            v-if="row.section === 'runs' && (row.refusals || row.guard)"
            class="notes dim"
          >
            <span v-if="row.refusals">{{ row.refusals }} refusals</span>
            <span v-if="row.guard"> guard: {{ row.guard.bypasses }} bypasses, {{ row.guard.blocking }} blocking</span>
          </div>
          <table v-if="row.processes?.length" class="procs">
            <thead><tr><th>pid</th><th>age</th><th>command</th></tr></thead>
            <tbody>
              <tr v-for="p in row.processes" :key="p.pid">
                <td>{{ p.pid }}</td>
                <td>{{ age(p.age_s) }}</td>
                <td class="mono">{{ p.command }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.factory {
  margin-bottom: 28px;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px;
  margin-bottom: 12px;
}
.spacer {
  flex: 1;
}
.count {
  font-weight: 700;
}
.count.red {
  color: var(--red);
}
.count.amber {
  color: var(--amber);
}
.dim {
  color: var(--dim);
}
.switch {
  display: inline-flex;
  gap: 6px;
  align-items: center;
}
.errors {
  color: var(--red);
  margin: 0 0 12px;
  padding-left: 18px;
}
.rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: flex;
  gap: 12px;
  padding: 10px 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
}
.dot {
  flex: none;
  width: 11px;
  height: 11px;
  margin-top: 5px;
  border-radius: 50%;
}
.dot.red {
  background: var(--red);
}
.dot.amber {
  background: var(--amber);
}
.dot.ok {
  background: var(--green);
}
.body {
  min-width: 0;
  flex: 1;
}
.head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
}
.chip {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--faint);
  border: 1px solid var(--border-soft);
  border-radius: 4px;
  padding: 0 6px;
}
.title {
  font-weight: 700;
}
.figure {
  color: var(--dim);
}
.problem {
  margin-top: 2px;
}
.row.red .problem {
  color: var(--red);
}
.row.amber .problem {
  color: var(--amber);
}
.next.cmd {
  font-family: var(--mono);
}
.notes {
  font-size: 14px;
}
.procs {
  margin-top: 6px;
  font-size: 14px;
  border-collapse: collapse;
}
.procs th,
.procs td {
  text-align: left;
  padding: 1px 12px 1px 0;
}
.mono {
  font-family: var(--mono);
  word-break: break-all;
}
</style>
