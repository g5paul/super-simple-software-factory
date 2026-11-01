/**
 * What every registered repo spent in the last 24 hours, against the machine's
 * max_daily_cost in limits.yaml beside the registry (parallel-worktree-runs
 * decisions 73 to 82). The same sum and clear-time walk as budget.py's
 * _machine_over, except that a call still in flight counts only once it ends.
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { MachineDay } from "../shared/types.ts";
import { type DayCall, daySpend } from "./day.ts";
import { registryPath } from "./projects.ts";

export function limitsPath(): string {
  return join(dirname(registryPath()), "limits.yaml");
}

/** The cap, or why the file can't be read: budget.py stops every run on a broken file, so the panel says so. */
export function readMachineCap(path: string = limitsPath()): { cap: number | null; error: string | null } {
  if (!existsSync(path)) return { cap: null, error: null };
  try {
    const data = Bun.YAML.parse(readFileSync(path, "utf8")) as Record<string, unknown> | null;
    const cap = data?.max_daily_cost;
    if (cap === undefined || cap === null) return { cap: null, error: null };
    if (typeof cap === "number" && cap > 0) return { cap, error: null };
    return { cap: null, error: `max_daily_cost must be a positive number, not ${JSON.stringify(cap)}` };
  } catch (error) {
    return { cap: null, error: (error as Error).message };
  }
}

/** Each repo's calls in the window, oldest first within each. */
export function machineDay(repos: { name: string; calls: DayCall[] }[], cap: number | null,
                           error: string | null, now: number, path: string = limitsPath()): MachineDay {
  const calls = repos.flatMap((r) => r.calls).toSorted((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const shares = repos
    .map((r) => ({ name: r.name, spent: r.calls.reduce((sum, c) => sum + c.cost, 0) }))
    .toSorted((a, b) => b.spent - a.spent);
  return { ...daySpend(calls, cap, now), repos: shares, error, path };
}
