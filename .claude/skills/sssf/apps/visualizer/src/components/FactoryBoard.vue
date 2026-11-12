<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import type { FactoryBoard, FactoryRow, FactorySection } from '../lib/types'
import { fetchFactory } from '../lib/api'
import { projectHref } from '../lib/router'

const KEY = 'sssf.factory.listed'
const SECTIONS: { id: FactorySection; label: string }[] = [
  { id: 'projects', label: 'Projects' },
  { id: 'machine', label: 'Machine' },
  { id: 'runs', label: 'Runs' },
]

const board = shallowRef<FactoryBoard | null>(null)
const apiError = ref<string | null>(null)
const listed = ref(readListed())
// The tile that filters the list, or null for every section.
const only = ref<FactorySection | null>(null)
const showOk = ref(false)
const open = ref(new Set<string>())

let timer: ReturnType<typeof setInterval> | undefined
let inflight = false
const previousTitle = document.title

function readListed(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    return false
  }
}

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
  try {
    localStorage.setItem(KEY, on ? '1' : '0')
  } catch {
    // a private window: the switch still works for this visit
  }
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

/** One tile per section: its counts, and the worst row's first problem. */
const tiles = computed(() =>
  SECTIONS.map(({ id, label }) => {
    const rows = board.value?.rows.filter((r) => r.section === id) ?? []
    const red = rows.filter((r) => r.level === 'red').length
    const amber = rows.filter((r) => r.level === 'amber').length
    const worst = rows.find((r) => r.level !== 'ok')
    const level = red ? 'red' : amber ? 'amber' : 'ok'
    return {
      id,
      label,
      red,
      amber,
      ok: rows.length - red - amber,
      level,
      line: worst ? (worst.problems[0] ?? worst.title) : rows.length ? 'all ok' : 'nothing to check',
    }
  }),
)

const inView = computed(() => board.value?.rows.filter((r) => !only.value || r.section === only.value) ?? [])
const shown = computed(() => inView.value.filter((r) => showOk.value || r.level !== 'ok'))
const okCount = computed(() => inView.value.filter((r) => r.level === 'ok').length)

function pick(id: FactorySection) {
  only.value = only.value === id ? null : id
}

function toggle(key: string) {
  const next = new Set(open.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  open.value = next
}

/** A run row's title without the repo, which its own column already shows. */
function label(row: FactoryRow): string {
  if (row.section === 'runs' && row.repo && row.title.startsWith(`${row.repo} `)) {
    return row.title.slice(row.repo.length + 1).replace(/\badw_/, '')
  }
  return row.title
}

/** The repo column: "hub" for the hub itself, whose name is long and on most runs. */
function repoName(row: FactoryRow): string {
  if (row.section !== 'runs' || !row.repo) return ''
  const hub = board.value?.hub.replace(/\/+$/, '').split('/').pop()
  return row.repo === hub ? 'hub' : row.repo
}

function summary(row: FactoryRow): string {
  return row.figure ?? row.problems[0] ?? ''
}

function runNotes(row: FactoryRow): string[] {
  const out: string[] = []
  if (row.refusals) out.push(`${row.refusals} refusal${row.refusals === 1 ? '' : 's'}`)
  if (row.guard) out.push(`guard: ${row.guard.bypasses} bypasses, ${row.guard.blocking} blocking`)
  return out
}

function titleHref(row: FactoryRow): string | null {
  if (!row.repo) return null
  const base = projectHref(row.repo)
  return row.adw_id ? `${base}/${encodeURIComponent(row.adw_id)}` : base
}

function isCommand(next: string): boolean {
  return next.includes('`') || /^(sudo|just|uv|bun) /.test(next)
}

function clock(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString()
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
        <span class="dim small" :title="`board ${board.generated_at}, scripts ${board.scripts_at ?? '?'}`">
          updated {{ clock(board.generated_at) }}
        </span>
      </template>
      <span v-else class="dim">reading the factory…</span>
      <span class="spacer" />
      <span v-if="board && listed && board.private_names" class="dim small">
        {{ board.hidden }} unlisted repos hidden
      </span>
      <label
        class="switch"
        :title="board && !board.private_names ? 'the hub has no .git/sssf-private-names' : ''"
      >
        <input v-model="listed" type="checkbox" :disabled="board !== null && !board.private_names" />
        listed only
      </label>
      <button type="button" class="refresh" @click="load(true)">refresh now</button>
    </div>

    <ul v-if="board && board.errors.length" class="errors">
      <li v-for="e in board.errors" :key="e">{{ e }}</li>
    </ul>

    <div v-if="board" class="tiles">
      <button
        v-for="t in tiles"
        :key="t.id"
        type="button"
        class="tile"
        :class="[t.level, { picked: only === t.id, faded: only !== null && only !== t.id }]"
        :aria-pressed="only === t.id"
        :title="only === t.id ? 'show every section' : `show only ${t.label.toLowerCase()}`"
        @click="pick(t.id)"
      >
        <span class="tile-head">{{ t.label }}</span>
        <span class="tile-counts">
          <span class="n red" :class="{ zero: !t.red }"><i class="dot red" />{{ t.red }}</span>
          <span class="n amber" :class="{ zero: !t.amber }"><i class="dot amber" />{{ t.amber }}</span>
          <span class="n ok"><i class="dot ok" />{{ t.ok }}</span>
        </span>
        <span class="tile-line">{{ t.line }}</span>
      </button>
    </div>

    <ul v-if="board" class="items">
      <li v-for="row in shown" :key="row.key" class="item" :class="[row.level, { open: open.has(row.key) }]">
        <button type="button" class="line" :aria-expanded="open.has(row.key)" @click="toggle(row.key)">
          <i class="dot" :class="row.level" :title="row.level" />
          <span class="sec">{{ row.section }}</span>
          <span class="repo" :title="row.repo ?? ''">{{ repoName(row) }}</span>
          <span class="what">{{ label(row) }}</span>
          <span class="sum">{{ summary(row) }}</span>
          <span class="chev" aria-hidden="true">›</span>
        </button>
        <div v-if="open.has(row.key)" class="detail">
          <div v-for="p in row.problems" :key="p" class="problem">{{ p }}</div>
          <div v-if="row.next" class="next">
            <span class="dim">next</span>
            <span :class="{ mono: isCommand(row.next) }">{{ row.next }}</span>
          </div>
          <div v-if="row.notes.length || runNotes(row).length" class="notes dim">
            {{ [...row.notes, ...runNotes(row)].join(' · ') }}
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
          <a v-if="titleHref(row)" :href="titleHref(row)!" class="open-link">
            open {{ row.adw_id ? `run ${row.adw_id}` : row.repo }} ›
          </a>
        </div>
      </li>
      <li v-if="!shown.length" class="empty dim">nothing red or amber{{ only ? ` in ${only}` : '' }}</li>
    </ul>

    <button v-if="board && okCount" type="button" class="ok-toggle dim" @click="showOk = !showOk">
      {{ showOk ? 'hide' : 'show' }} {{ okCount }} ok
    </button>
  </section>
</template>

<style scoped>
.factory {
  margin: 16px 24px 8px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.strip {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 14px;
}
.spacer {
  flex: 1;
}
.small {
  font-size: 14px;
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
.refresh {
  font: inherit;
  font-size: 14px;
  color: var(--dim);
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px 10px;
  cursor: pointer;
}
.errors {
  color: var(--red);
  margin: 0;
  padding-left: 18px;
}

.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}
.tile {
  font: inherit;
  color: inherit;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-left: 3px solid var(--green);
  border-radius: 8px;
  cursor: pointer;
  min-width: 0;
}
.tile.red {
  border-left-color: var(--red);
}
.tile.amber {
  border-left-color: var(--amber);
}
.tile.picked {
  background: var(--panel-2);
  border-color: var(--dim);
}
.tile.faded {
  opacity: 0.55;
}
.tile-head {
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0.07em;
  color: var(--faint);
}
.tile-counts {
  display: flex;
  gap: 16px;
  font-size: 22px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.n {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.n.red {
  color: var(--red);
}
.n.amber {
  color: var(--amber);
}
.n.ok {
  color: var(--faint);
  font-weight: 400;
}
.n.zero {
  color: var(--faint);
  font-weight: 400;
}
.tile-line {
  font-size: 14px;
  color: var(--dim);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dot {
  flex: none;
  display: inline-block;
  width: 9px;
  height: 9px;
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
.n .dot {
  width: 8px;
  height: 8px;
}

.items {
  list-style: none;
  margin: 0;
  padding: 0;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.item + .item {
  border-top: 1px solid var(--border-soft);
}
.line {
  font: inherit;
  color: inherit;
  width: 100%;
  display: grid;
  grid-template-columns: 9px 76px minmax(0, 15ch) minmax(0, 1.2fr) minmax(0, 1fr) 14px;
  align-items: center;
  gap: 12px;
  padding: 7px 14px;
  background: none;
  border: 0;
  text-align: left;
  cursor: pointer;
}
.line:hover,
.item.open .line {
  background: var(--panel-2);
}
.sec {
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--faint);
}
.repo,
.what,
.sum {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.repo {
  color: var(--faint);
  font-size: 14px;
}
.what {
  font-weight: 600;
}
.sum {
  color: var(--dim);
  font-size: 14px;
}
.item.red .sum {
  color: var(--red);
}
.item.amber .sum {
  color: var(--amber);
}
.chev {
  color: var(--faint);
  transition: transform 0.12s;
}
.item.open .chev {
  transform: rotate(90deg);
}
.detail {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 4px 14px 12px 35px;
  background: var(--panel-2);
  font-size: 14px;
}
.item.red .problem {
  color: var(--red);
}
.item.amber .problem {
  color: var(--amber);
}
.next {
  display: flex;
  gap: 8px;
}
.mono {
  font-family: var(--mono);
  word-break: break-all;
}
.procs {
  margin-top: 4px;
  border-collapse: collapse;
}
.procs th,
.procs td {
  text-align: left;
  padding: 1px 12px 1px 0;
}
.open-link {
  align-self: flex-start;
  margin-top: 2px;
}
.empty {
  padding: 10px 14px;
}
.ok-toggle {
  align-self: flex-start;
  font: inherit;
  font-size: 14px;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
  text-decoration: underline;
}

@media (max-width: 720px) {
  .factory {
    margin: 12px 16px 8px;
  }
  .line {
    grid-template-columns: 9px minmax(0, 1fr) 14px;
    row-gap: 2px;
  }
  .sec,
  .repo {
    display: none;
  }
  .sum {
    grid-column: 2;
  }
}
</style>
