/**
 * What remote VM runs used of each provider's free allowance, over every
 * registered repo: an allowance belongs to a Vercel or E2B team, not to one
 * repo. The same sums as `just sandbox-usage` (adw_modules/sandbox_usage.py),
 * with the same Hobby numbers, so the gauge and the report agree.
 *
 * SSSF's runs only. VMs made outside SSSF don't show, and the provider's own
 * dashboard is the bill.
 */
import type { SandboxAllowance, SandboxEnd, SandboxSpan, SandboxUsage } from "../shared/types.ts";
import { SPAN_MS, type Span } from "../shared/spans.ts";

// vercel.com/docs/sandbox/pricing (2026-09-10) and e2b.dev/pricing (2026-11-02).
export const VERCEL = { cpu_hours: 5, gb_hours: 420, creations: 5000 };
export const E2B = { credit_usd: 100 };
const E2B_USD_PER_SECOND = 2 * 0.000014 + 4 * 0.0000045;   // the sssf template's 2 vCPUs and 4 GiB
const WINDOW_DAYS = 30;

type End = SandboxEnd & { provider?: string; vm?: string };

export interface RepoEnds {
  name: string;
  ends: { at: string; payload_json: string | null }[];
}

function blank(provider: "vercel" | "e2b"): SandboxAllowance {
  return {
    provider,
    window_days: provider === "vercel" ? WINDOW_DAYS : null,
    runs: 0, vm_seconds: 0, cpu_seconds: 0, cpu_unknown: 0, gb_hours: 0, cost_usd: 0,
    allowed: provider === "vercel" ? { ...VERCEL } : { ...E2B },
    repos: [],
  };
}

function parse(json: string | null): End | null {
  if (!json) return null;
  try {
    return JSON.parse(json) as End;
  } catch {
    return null;
  }
}

type Tally = Pick<SandboxAllowance, "runs" | "vm_seconds" | "cpu_seconds" | "gb_hours" | "cost_usd" | "repos">
  & { cpu_unknown?: number };

function add(t: Tally, end: End, repo: string): void {
  const seconds = end.vm_seconds ?? 0;
  t.runs += 1;
  t.vm_seconds += seconds;
  if (end.cpu_seconds === undefined || end.cpu_seconds === null) {
    if (t.cpu_unknown !== undefined) t.cpu_unknown += 1;
  } else t.cpu_seconds += end.cpu_seconds;
  if (end.provider === "vercel") t.gb_hours += end.memory_gb_hours ?? (4 * seconds) / 3600;
  else t.cost_usd += end.vm_cost_usd ?? seconds * E2B_USD_PER_SECOND;
  const share = t.repos.find((r) => r.name === repo);
  if (share) share.runs += 1;
  else t.repos.push({ name: repo, runs: 1 });
}

function spanBlank(provider: "vercel" | "e2b"): SandboxSpan {
  return { provider, runs: 0, vm_seconds: 0, cpu_seconds: 0, gb_hours: 0, cost_usd: 0, repos: [] };
}

/**
 * Vercel over the last 30 days, E2B over all time, against their allowances;
 * and each provider's runs in the picker's span, apart. A VM counted once,
 * however often its trace is read.
 */
export function sandboxUsage(repos: RepoEnds[], now: number, span: Span = "30d"): SandboxUsage {
  const since = now - WINDOW_DAYS * 24 * 3_600_000;
  const spanSince = now - SPAN_MS[span];
  const totals = { vercel: blank("vercel"), e2b: blank("e2b") };
  const inSpan = { vercel: spanBlank("vercel"), e2b: spanBlank("e2b") };
  const seen = new Set<string>();
  for (const repo of repos) {
    for (const row of repo.ends) {
      const end = parse(row.payload_json);
      if (end?.provider !== "vercel" && end?.provider !== "e2b") continue;
      const key = `${end.provider}:${end.vm}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const at = Date.parse(row.at);
      if (end.provider === "e2b" || at >= since) add(totals[end.provider], end, repo.name);
      if (at >= spanSince) add(inSpan[end.provider], end, repo.name);
    }
  }
  return {
    providers: [totals.vercel, totals.e2b].filter((t) => t.runs > 0 || inSpan[t.provider].runs > 0),
    span,
    in_span: [inSpan.vercel, inSpan.e2b].filter((t) => t.runs > 0),
  };
}
