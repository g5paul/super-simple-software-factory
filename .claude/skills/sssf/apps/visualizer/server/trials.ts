/**
 * A trial's page and a repo's roster leaderboard (trials.md decisions 24 and 25).
 *
 * trial.py computes the gates, ranks, and suggestion once, when its panel
 * runs, and records them as a trial_scorecard event. This reads that rather
 * than repeating the logic. Before a panel has run, the page shows the
 * attempts as their runs stand.
 */
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { RosterRow, RunTrial, SessionStatus, TrialAttemptCard, TrialView } from "../shared/types.ts";
import { payload, type RunEvent } from "./runview.ts";

/** An attempt's trial tag, joined to its run. */
export interface AttemptRow {
  adw_id: string;
  payload_json: string;
  status: SessionStatus | null;
  cost: number;
  tokens: number;
  started_at: string | null;
  ended_at: string | null;
  request: string | null;
}

type Scorecard = Omit<TrialView, "scored">;

function seconds(start: string | null, end: string | null): number | null {
  return start && end ? (Date.parse(end) - Date.parse(start)) / 1000 : null;
}

/** The trial as its runs stand, with the panel's scorecard when there is one. */
export function trialView(trial: string, rows: AttemptRow[], card: Scorecard | null, merged: string | null): TrialView {
  const now = new Map(rows.map((r) => [r.adw_id, r]));
  if (card) {
    const attempts = card.attempts.map((a) => ({ ...a, status: now.get(a.adw_id)?.status ?? a.status, live: now.get(a.adw_id)?.status === "running" }));
    return { ...card, attempts, merged, running: attempts.some((a) => a.live), scored: true };
  }
  const attempts: TrialAttemptCard[] = rows.map((r) => {
    const tag = JSON.parse(r.payload_json) as Partial<TrialAttemptCard> & { index?: number };
    return {
      adw_id: r.adw_id, roster: tag.roster ?? "?", roster_file: tag.roster_file ?? "",
      roster_sha256: tag.roster_sha256 ?? "", coding_agent: tag.coding_agent, family: tag.family ?? "",
      status: r.status, cost: r.cost, tokens: r.tokens, seconds: seconds(r.started_at, r.ended_at),
      checks: null, live: r.status === "running", review: null, rulings: [],
      rank: null, rank_others: null, passed: false,
    };
  });
  const running = attempts.some((a) => a.live);
  const first = rows[0] ? JSON.parse(rows[0].payload_json) as { base?: string } : {};
  return {
    trial, request: rows[0]?.request ?? "", base: first.base ?? "",
    families: [...new Set(attempts.map((a) => a.family))],
    panel: null, panel_status: null, panel_cost: 0,
    total_cost: attempts.reduce((s, a) => s + a.cost, 0),
    suggested: null,
    why: running ? "attempts are still running, so there is no panel yet" : "no panel has run yet",
    merged, running, attempts, scored: false,
  };
}

function median(values: number[]): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid]! : (s[mid - 1]! + s[mid]!) / 2;
}

/** One row per roster name and file hash, over every scored trial. Most wins first. */
export function leaderboard(cards: Scorecard[], winners: Set<string>, current: (rosterFile: string) => string | null): RosterRow[] {
  const rows = new Map<string, { row: RosterRow; trials: Set<string>; costs: number[]; times: number[] }>();
  for (const card of cards) {
    for (const a of card.attempts) {
      const key = `${a.roster}\u0000${a.roster_sha256}`;
      let entry = rows.get(key);
      if (!entry) {
        entry = {
          row: { roster: a.roster, sha256: a.roster_sha256, current: current(a.roster_file) === a.roster_sha256,
                 trials: 0, attempts: 0, passed: 0, wins: 0, median_cost: null, median_seconds: null },
          trials: new Set(), costs: [], times: [],
        };
        rows.set(key, entry);
      }
      entry.trials.add(card.trial);
      if (a.coding_agent) entry.row.coding_agent = a.coding_agent;
      entry.row.attempts += 1;
      if (winners.has(a.adw_id)) entry.row.wins += 1;
      if (a.passed) {
        entry.row.passed += 1;
        entry.costs.push(a.cost);
        if (a.seconds !== null) entry.times.push(a.seconds);
      }
    }
  }
  return [...rows.values()]
    .map(({ row, trials, costs, times }) => ({ ...row, trials: trials.size, median_cost: median(costs), median_seconds: median(times) }))
    .sort((a, b) => b.wins - a.wins || b.passed - a.passed || a.roster.localeCompare(b.roster));
}

/** The sha256 of a roster file in a repo, or null when it can't be read. */
export function rosterSha(root: string, rosterFile: string): string | null {
  if (!rosterFile || rosterFile.includes("..")) return null;
  try {
    return createHash("sha256").update(readFileSync(join(root, rosterFile))).digest("hex");
  } catch {
    return null;
  }
}

/** Whether a run is an attempt of a trial or a trial's panel. */
export function runTrial(events: RunEvent[]): RunTrial | null {
  for (const e of events) {
    if (e.name === "trial") {
      const p = payload<{ trial?: string; roster?: string; coding_agent?: string }>(e);
      if (p?.trial) return { trial: p.trial, role: "attempt", roster: p.roster ?? null, coding_agent: p.coding_agent };
    }
    if (e.name === "trial_panel") {
      const p = payload<{ trial?: string }>(e);
      if (p?.trial) return { trial: p.trial, role: "panel", roster: null };
    }
  }
  return null;
}
