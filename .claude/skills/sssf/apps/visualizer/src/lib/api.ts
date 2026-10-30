import type {
  AccountUsage,
  Envelope,
  EventRow,
  EventsPage,
  GateResult,
  HealthResponse,
  ProjectsResponse,
  PromptsResponse,
  SessionDetail,
  SessionSummary,
} from './types'
import { useRoute } from './router'

const route = useRoute()

/** The current project's API root. Every session call is scoped to it. */
function base(): string {
  return `/api/projects/${encodeURIComponent(route.value.project ?? '')}`
}

async function getJson(url: string): Promise<unknown> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`GET ${url} → ${res.status}`)
  return res.json()
}

export function fetchSessions(): Promise<SessionSummary[]> {
  return getJson(`${base()}/sessions`) as Promise<SessionSummary[]>
}

export async function fetchSession(adwId: string): Promise<SessionDetail> {
  const detail = (await getJson(`${base()}/sessions/${encodeURIComponent(adwId)}`)) as SessionDetail
  return {
    session: detail.session,
    caps: detail.caps ?? null,
    stop: detail.stop ?? null,
    tickets: detail.tickets ?? null,
    worktree: detail.worktree ?? null,
    usage: detail.usage ?? { read: 0, written: 0 },
    phases: detail.phases ?? [],
    agents: detail.agents ?? [],
  }
}

export async function fetchEvents(adwId: string, after: number, limit = 500): Promise<EventsPage> {
  const page = (await getJson(
    `${base()}/sessions/${encodeURIComponent(adwId)}/events?after=${after}&limit=${limit}`,
  )) as EventsPage | EventRow[]
  if (Array.isArray(page)) {
    const cursor = page.reduce((max, e) => Math.max(max, e.rowid), after)
    return { events: page, cursor, has_more: page.length === limit }
  }
  return { events: page.events ?? [], cursor: page.cursor ?? after, has_more: page.has_more ?? false }
}

/** Archive a run out of the review list (or restore it with archived=false). */
export async function archiveSession(adwId: string, archived = true): Promise<void> {
  const url = `${base()}/sessions/${encodeURIComponent(adwId)}/archive`
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ archived }),
  })
  if (!res.ok) throw new Error(`POST ${url} → ${res.status}`)
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson(`${base()}/health`) as Promise<HealthResponse>
}

// PhaseDetail imports the prompts type from here alongside fetchPrompts.
export type { PromptsResponse }

export async function fetchPrompts(adwId: string, agent: string): Promise<PromptsResponse> {
  const res = await fetch(
    `${base()}/sessions/${encodeURIComponent(adwId)}/agents/${encodeURIComponent(agent)}/prompts`,
  )
  // Not recorded (or endpoint not deployed yet) renders as "no prompts", not an error.
  if (res.status === 404) return { system: null, user: null }
  if (!res.ok) throw new Error(`GET prompts → ${res.status}`)
  const data = (await res.json()) as Partial<PromptsResponse>
  return { system: data.system ?? null, user: data.user ?? null }
}

export function fetchEnvelopes(adwId: string): Promise<Envelope[]> {
  return getJson(`${base()}/sessions/${encodeURIComponent(adwId)}/envelopes`) as Promise<Envelope[]>
}

export function fetchGates(adwId: string): Promise<GateResult[]> {
  return getJson(`${base()}/sessions/${encodeURIComponent(adwId)}/gates`) as Promise<GateResult[]>
}

export function fetchProjects(): Promise<ProjectsResponse> {
  return getJson('/api/projects') as Promise<ProjectsResponse>
}

export function fetchAccount(): Promise<AccountUsage> {
  return getJson('/api/account') as Promise<AccountUsage>
}
