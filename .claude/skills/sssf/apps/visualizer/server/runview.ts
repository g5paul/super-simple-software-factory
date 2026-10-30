/**
 * What the session view needs beyond the raw rows: the caps a run was held to,
 * why it stopped, where a `--worktree` run worked, and, for an adw_tickets run,
 * its phases grouped by ticket.
 *
 * Pure functions over phases and events, so they test without a database.
 * Everything is derived from what the tracer already wrote: a run from before
 * these events existed simply has no caps and no ticket table.
 */
import type { Phase, RunCaps, RunLanded, RunWorktree, SessionStatus, StopReason, TicketRow, TicketRun } from "../shared/types.ts";

/** The event columns these functions read. */
export interface RunEvent {
  type: string | null;
  name: string | null;
  phase_id: string | null;
  payload_json: string | null;
}

/** Stops that name their own reason, most specific first. */
const STOP_EVENTS = ["call_budget_stop", "budget_stop", "rate_limit_stop", "not_accepted"] as const;

/** A ticket phase's name: t03_build, t03_verify_2, t03_fix_1, t03_commit. */
const TICKET_PHASE = /^t(\d{2,3})_(build|verify_\d+|fix_\d+|commit)$/;

function payload<T>(e: RunEvent): T | null {
  if (!e.payload_json) return null;
  try {
    return JSON.parse(e.payload_json) as T;
  } catch {
    return null;
  }
}

function latest(events: RunEvent[], name: string): RunEvent | undefined {
  return events.findLast((e) => e.name === name);
}

export function caps(events: RunEvent[]): RunCaps | null {
  const e = latest(events, "run_caps");
  return e ? payload<RunCaps>(e) : null;
}

/** The events that end a worktree run's branch, and the state each one means. */
const LANDED = { worktree_merged: "merged", worktree_discarded: "discarded" } as const;

/**
 * Where a `--worktree` run worked, and whether `just merge` or `just discard`
 * has ended its branch. null for a run on the checked-out branch.
 */
export function worktree(events: RunEvent[]): RunWorktree | null {
  const e = latest(events, "worktree");
  const p = e && payload<Omit<RunWorktree, "landed">>(e);
  if (!p?.branch) return null;
  const end = events.findLast((ev) => ev.name !== null && ev.name in LANDED);
  const landed: RunLanded | null = end
    ? { state: LANDED[end.name as keyof typeof LANDED], ...payload<Omit<RunLanded, "state">>(end) }
    : null;
  return { ...p, landed };
}

/**
 * Why a failed run stopped. A cap or a rate limit says so in its own event;
 * a run whose phases passed but whose result was not accepted says why in
 * not_accepted; anything else is the first phase that failed.
 */
export function stopReason(events: RunEvent[], phases: Phase[], status: SessionStatus | null): StopReason | null {
  if (status !== "fail") return null;
  for (const kind of STOP_EVENTS) {
    const e = latest(events, kind);
    if (!e) continue;
    const p = payload<{ error?: string; reason?: string }>(e);
    return { kind, message: p?.error ?? p?.reason ?? kind, phase_id: e.phase_id || null };
  }
  const failed = phases.find((p) => p.status === "fail");
  if (!failed) return null;
  return {
    kind: "phase_failed",
    message: `${failed.name}: ${failed.error ?? "failed"}`,
    phase_id: failed.phase_id,
  };
}

/** One row per ticket, in build order, or null when this is not a ticket run. */
export function ticketRun(events: RunEvent[], phases: Phase[]): TicketRun | null {
  const listing = latest(events, "tickets");
  const list = listing && payload<{ feature: string; folder: string; tickets: { number: string; title: string; done: boolean }[] }>(listing);
  if (!list) return null;

  const costByPhase = new Map<string, number>();
  for (const e of events) {
    if (e.type !== "agent_end" || !e.phase_id) continue;
    const cost = payload<{ cost?: number }>(e)?.cost ?? 0;
    costByPhase.set(e.phase_id, (costByPhase.get(e.phase_id) ?? 0) + cost);
  }

  const byTicket = new Map<string, Phase[]>();
  for (const p of phases) {
    const m = TICKET_PHASE.exec(p.name ?? "");
    if (!m) continue;
    byTicket.set(m[1], [...(byTicket.get(m[1]) ?? []), p]);
  }

  const tickets: TicketRow[] = list.tickets.map((t) => {
    const own = byTicket.get(t.number) ?? [];
    return {
      number: t.number,
      title: t.title,
      status: ticketStatus(t.done, own),
      retries: own.filter((p) => /_fix_\d+$/.test(p.name ?? "")).length,
      cost: own.reduce((sum, p) => sum + (costByPhase.get(p.phase_id) ?? 0), 0),
      phase_id: own[0]?.phase_id ?? null,
    };
  });
  return { feature: list.feature, folder: list.folder, tickets };
}

function ticketStatus(doneBefore: boolean, own: Phase[]): TicketRow["status"] {
  if (doneBefore) return "done_before";
  if (own.length === 0) return "queued";
  if (own.some((p) => p.status === "fail")) return "fail";
  if (own.some((p) => p.name?.endsWith("_commit") && p.status === "success")) return "done";
  if (own.some((p) => p.status === "running" || p.status === "queued")) return "running";
  return "stopped";
}
