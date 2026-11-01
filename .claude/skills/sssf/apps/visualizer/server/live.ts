/**
 * What the live runs spend together, against repo.yaml's max_live_cost
 * (parallel-worktree-runs decisions 66 to 72). The same rows and the same
 * liveness test as worktree.py's live_spend, so the gauge and check_live
 * agree, except that a call still in flight counts only once it ends.
 */
import { hostname } from "node:os";
import type { LiveSpend } from "../shared/types.ts";
import { readRepoCap } from "./day.ts";

/** One open adw process row: its run, pid, host, the session's status, and what its attempt has spent. */
export interface LiveRow {
  adw_id: string;
  pid: number | null;
  host: string | null;
  status: string | null;
  cost: number;
}

/** worktree.py's _alive: a pid that exists, even one we may not signal. */
export function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === "EPERM";
  }
}

export function readLiveCap(root: string): number | null {
  return readRepoCap(root, "max_live_cost");
}

/** A pid is tested on its own host; a row from another host trusts its session's status. */
export function liveSpend(rows: LiveRow[], cap: number | null,
                          alive: (pid: number) => boolean = pidAlive, here: string = hostname()): LiveSpend {
  const spent = new Map<string, number>();
  for (const row of rows) {
    const live = row.pid && (row.host || here) === here ? alive(row.pid) : row.status === "running";
    if (live) spent.set(row.adw_id, Math.max(spent.get(row.adw_id) ?? 0, row.cost));
  }
  const runs = [...spent].map(([adw_id, cost]) => ({ adw_id, cost })).sort((a, b) => b.cost - a.cost);
  return { spent: runs.reduce((sum, r) => sum + r.cost, 0), cap, runs };
}
