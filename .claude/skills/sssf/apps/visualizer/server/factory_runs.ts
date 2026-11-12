/** The factory board's run rows: the last day's runs, against `phase_times.py --json`. */
import type { FactoryLevel, FactoryRow } from "../shared/types.ts";
import { COST_AMBER, SLOW_AMBER, SLOW_RED } from "./factory_limits.ts";

export interface SlowItem {
  kind: "phase" | "run" | "cost";
  adw_id: string;
  chain?: string;
  status?: string;
  start?: string;
  ratio: number;
  name?: string;
  seconds?: number;
  p90_s?: number;
  running?: boolean;
  cost?: number;
  p90?: number;
}
export interface PhaseTimesJson {
  now?: string;
  repos: { repo: string; chains?: unknown[]; slow: SlowItem[]; left_running?: unknown[] }[];
}

export function parsePhaseTimes(stdout: string): PhaseTimesJson {
  const data = JSON.parse(stdout) as PhaseTimesJson;
  if (!data || !Array.isArray(data.repos)) throw new Error("no repos array in the output");
  return data;
}

export interface FactoryRunFacts {
  adw_id: string;
  adw_name: string;
  status: string | null;
  started_at: string | null;
  ended_at: string | null;
  last_error: string | null;
  live: boolean | null;
  refusals: number;
  guard: { bypasses: number; blocking: number } | null;
  spec_blocked: boolean;
}

export function runRows(perRepo: { repo: string; runs: FactoryRunFacts[] }[], slow: PhaseTimesJson): FactoryRow[] {
  const rows: FactoryRow[] = [];
  for (const { repo, runs } of perRepo) {
    const items = slow.repos.find((r) => r.repo === repo)?.slow ?? [];
    for (const run of runs) {
      const red: string[] = [];
      const amber: string[] = [];
      let figure: string | null = null;
      const fig = (f: string) => { figure ??= f; };
      if (run.status === "fail") red.push(`failed${run.last_error ? ` (${run.last_error})` : ""}`);
      if (run.status === "running" && run.live === false) red.push("marked running but no live process: it crashed");
      if (run.guard && (run.guard.blocking > 0 || run.guard.bypasses > 0)) {
        red.push(`guard found ${run.guard.blocking} blocking, ${run.guard.bypasses} bypasses`);
        fig(`${run.guard.bypasses} bypasses`);
      }
      for (const item of items.filter((i) => i.adw_id === run.adw_id)) {
        if (item.kind === "cost") {
          if (item.ratio >= COST_AMBER) {
            amber.push(`cost $${(item.cost ?? 0).toFixed(2)}, ${item.ratio.toFixed(1)}× its usual`);
            fig(`${item.ratio.toFixed(1)}× its p90`);
          }
          continue;
        }
        const what = item.kind === "phase" ? `phase ${item.name ?? "?"}` : "run";
        if (item.ratio >= SLOW_RED) { red.push(`${what} took ${item.ratio.toFixed(1)}× its usual top`); fig(`${item.ratio.toFixed(1)}× its p90`); }
        else if (item.ratio >= SLOW_AMBER) { amber.push(`${what} took ${item.ratio.toFixed(1)}× its usual top`); fig(`${item.ratio.toFixed(1)}× its p90`); }
      }
      if (run.refusals > 0) amber.push(`${run.refusals} rule refusals`);
      if (run.spec_blocked) amber.push("a spec review blocked");
      const level: FactoryLevel = red.length ? "red" : amber.length ? "amber" : "ok";
      rows.push({
        section: "runs", key: `runs/${repo}/${run.adw_id}`, level,
        title: `${repo} ${run.adw_id} ${run.adw_name}`,
        problems: [...red, ...amber], figure,
        next: level === "ok" ? null : `open the run: #/${repo}/${run.adw_id}, or ask the ${repo} session`,
        repo, adw_id: run.adw_id,
        notes: [run.status ? `status: ${run.status}` : "status unknown"],
        refusals: run.refusals, guard: run.guard,
      });
    }
  }
  return rows;
}
