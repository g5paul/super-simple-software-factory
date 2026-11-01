/**
 * What a repo spent in the last 24 hours, against repo.yaml's max_daily_cost
 * (parallel-worktree-runs decisions 58 to 65). The same sum and clear-time
 * walk as budget.py's _daily_over, so the gauge and the check agree, except
 * that a call still in flight counts only once it ends.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { DaySpend } from "../shared/types.ts";

export const DAY_MS = 24 * 3600 * 1000;
const REPO_YAML = join("adws", "adw_sssf_config", "repo.yaml");

export interface DayCall {
  at: string;
  cost: number;
}

/** A cap from repo.yaml, read fresh, as the checks read it. Missing, unreadable, or not a positive number: no cap. */
export function readRepoCap(root: string, key: "max_daily_cost" | "max_live_cost"): number | null {
  try {
    const data = Bun.YAML.parse(readFileSync(join(root, REPO_YAML), "utf8")) as Record<string, unknown> | null;
    const cap = data?.[key];
    return typeof cap === "number" && cap > 0 ? cap : null;
  } catch {
    return null;
  }
}

export function readDailyCap(root: string): number | null {
  return readRepoCap(root, "max_daily_cost");
}

/** Calls oldest first, every one inside the window. */
export function daySpend(calls: DayCall[], cap: number | null, now: number): DaySpend {
  const spent = calls.reduce((sum, c) => sum + c.cost, 0);
  let clears_at: string | null = null;
  if (cap !== null && spent >= cap) {
    let left = spent;
    for (const c of calls) {               // oldest first: when enough has aged out
      left -= c.cost;
      if (left < cap) {
        clears_at = new Date(Date.parse(c.at) + DAY_MS).toISOString();
        break;
      }
    }
  }
  return { spent, cap, clears_at, since: new Date(now - DAY_MS).toISOString() };
}
