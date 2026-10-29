/**
 * The Claude account behind every project: how full its usage windows are,
 * and where the spend in them went.
 *
 * Two sources, because neither has the whole picture:
 *
 * - rate_limits.json, beside the project registry. adw_modules/account.py
 *   keeps the newest `rate_limit_event` an SSSF agent saw there: the 5-hour
 *   and 7-day utilization and their reset times. Interactive sessions never
 *   write one, so it is as fresh as the last SSSF send.
 * - Claude Code's transcripts, ~/.claude/projects/**.jsonl (or
 *   $CLAUDE_CONFIG_DIR/projects). Every API call is there with its model and
 *   token counts, SSSF agents' calls included. A call is SSSF's when its
 *   session id is in a registered repo's agent_sessions table.
 *
 * Transcripts only report tokens, so cost is an estimate (see RATES). Files
 * are read incrementally: each poll reads only what was appended.
 */
import { closeSync, existsSync, openSync, readFileSync, readSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import type { AccountUsage, LimitSample, LimitWindow, SpendRow, SpendWindow } from "../shared/types.ts";
import { registryPath } from "./projects.ts";

/**
 * Dollars per million input tokens at list price. The other token kinds are
 * priced off it with WEIGHTS. Fitted to the costUSD Claude Code itself reports
 * in SSSF result events (Oct 2026): Haiku matched exactly; Opus and Sonnet
 * stayed within 8% across runs. A model not listed gets no estimate.
 */
const RATES: Record<string, number> = {
  "claude-opus-5-5": 3.7,
  "claude-sonnet-5-5": 1.85,
  "claude-haiku-5-5": 0.1,
};
const WEIGHTS = { input: 1, cacheRead: 0.1, cacheWrite5m: 1.25, cacheWrite1h: 2, output: 5 };

const HOUR = 3_600_000;
const WINDOW_MS = { five_hour: 5 * HOUR, seven_day: 7 * 24 * HOUR };
// Older transcripts cannot fall in a 7-day window; skip them unread.
const KEEP_MS = 8 * 24 * HOUR;

export interface Tokens {
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite5m: number;
  cacheWrite1h: number;
}

interface Call extends Tokens {
  at: number;
  session: string;
  cwd: string;
  model: string;
}

interface FileState {
  offset: number;
  calls: Call[];
}

export function estimateCost(model: string, t: Tokens): number | null {
  const rate = RATES[model];
  if (rate === undefined) return null;
  const units = t.input * WEIGHTS.input + t.cacheRead * WEIGHTS.cacheRead
    + t.cacheWrite5m * WEIGHTS.cacheWrite5m + t.cacheWrite1h * WEIGHTS.cacheWrite1h + t.output * WEIGHTS.output;
  return (units / 1e6) * rate;
}

export function ratesFilePath(): string {
  return join(dirname(registryPath()), "rate_limits.json");
}

export function transcriptsDir(): string {
  return join(process.env.CLAUDE_CONFIG_DIR || join(homedir(), ".claude"), "projects");
}

function jsonlFiles(dir: string, out: string[] = []): string[] {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) jsonlFiles(path, out);
    else if (entry.name.endsWith(".jsonl")) out.push(path);
  }
  return out;
}

function parseCall(line: string): (Call & { key: string }) | null {
  if (!line.includes('"assistant"') || !line.includes('"usage"')) return null;
  let e;
  try {
    e = JSON.parse(line);
  } catch {
    return null;
  }
  const m = e?.message;
  const u = m?.usage;
  if (e?.type !== "assistant" || !u || !m.model || m.model === "<synthetic>") return null;
  const split = u.cache_creation;
  return {
    key: `${m.id}|${e.requestId}`,
    at: Date.parse(e.timestamp),
    session: String(e.sessionId ?? ""),
    cwd: String(e.cwd ?? ""),
    model: m.model,
    input: u.input_tokens ?? 0,
    output: u.output_tokens ?? 0,
    cacheRead: u.cache_read_input_tokens ?? 0,
    cacheWrite5m: split ? split.ephemeral_5m_input_tokens ?? 0 : 0,
    cacheWrite1h: split ? split.ephemeral_1h_input_tokens ?? 0 : u.cache_creation_input_tokens ?? 0,
  };
}

function readSample(path: string, now: number): LimitSample | null {
  if (!existsSync(path)) return null;
  let data;
  try {
    data = JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return null;
  }
  const info = data?.info ?? {};
  const windows = info.unifiedWindows ?? {};
  // A window that has reset says nothing about how full it is now.
  const live = (w: { utilization?: number; resetsAt?: number } | undefined): LimitWindow | null =>
    w && typeof w.utilization === "number" && (w.resetsAt ?? 0) * 1000 > now
      ? { utilization: w.utilization, resets_at: new Date((w.resetsAt ?? 0) * 1000).toISOString() }
      : null;
  return {
    recorded_at: String(data.recorded_at ?? ""),
    source: String(data.source ?? ""),
    age_s: Math.round((now - Date.parse(data.recorded_at)) / 1000),
    status: info.status ?? null,
    overage_status: info.overageStatus ?? null,
    five_hour: live(windows.five_hour),
    seven_day: live(windows.seven_day),
  };
}

function rows(calls: Call[], keyOf: (c: Call) => string): SpendRow[] {
  const by = new Map<string, SpendRow>();
  for (const c of calls) {
    const key = keyOf(c);
    const row = by.get(key) ?? { key, calls: 0, est_usd: 0, output_tokens: 0, input_tokens: 0 };
    row.calls += 1;
    row.est_usd += estimateCost(c.model, c) ?? 0;
    row.output_tokens += c.output;
    row.input_tokens += c.input + c.cacheRead + c.cacheWrite5m + c.cacheWrite1h;
    by.set(key, row);
  }
  return [...by.values()].toSorted((a, b) => b.est_usd - a.est_usd || a.key.localeCompare(b.key));
}

export class Account {
  private readonly files = new Map<string, FileState>();
  private readonly seen = new Set<string>();

  constructor(
    readonly transcripts: string = transcriptsDir(),
    readonly ratesFile: string = ratesFilePath(),
  ) {}

  /** Read what each transcript appended since the last call; a partial last line waits. */
  private refresh(now: number): Call[] {
    for (const path of jsonlFiles(this.transcripts)) {
      let stat;
      try {
        stat = statSync(path);
      } catch {
        continue;
      }
      if (now - stat.mtimeMs > KEEP_MS && !this.files.has(path)) continue;
      const state = this.files.get(path) ?? { offset: 0, calls: [] };
      this.files.set(path, state);
      if (stat.size <= state.offset) continue;
      const buf = Buffer.alloc(stat.size - state.offset);
      const fd = openSync(path, "r");
      try {
        readSync(fd, buf, 0, buf.length, state.offset);
      } finally {
        closeSync(fd);
      }
      const end = buf.lastIndexOf(0x0a);
      if (end === -1) continue;
      state.offset += end + 1;
      for (const line of buf.subarray(0, end).toString("utf8").split("\n")) {
        const call = parseCall(line);
        if (!call || this.seen.has(call.key) || Number.isNaN(call.at)) continue;
        this.seen.add(call.key);
        state.calls.push(call);
      }
    }
    return [...this.files.values()].flatMap((s) => s.calls);
  }

  summary(now: number, sssfSessions: Set<string>, projectOf: (cwd: string) => string): AccountUsage {
    const calls = this.refresh(now);
    const sample = readSample(this.ratesFile, now);
    const span = (live: LimitWindow | null, kind: "five_hour" | "seven_day"): SpendWindow => {
      const end = live ? Date.parse(live.resets_at) : now;
      const start = end - WINDOW_MS[kind];
      const inside = calls.filter((c) => c.at >= start && c.at <= Math.min(end, now));
      const unpriced = [...new Set(inside.map((c) => c.model).filter((m) => RATES[m] === undefined))];
      return {
        basis: live ? kind : "rolling",
        start: new Date(start).toISOString(),
        end: new Date(end).toISOString(),
        calls: inside.length,
        est_usd: inside.reduce((sum, c) => sum + (estimateCost(c.model, c) ?? 0), 0),
        by_kind: rows(inside, (c) => (sssfSessions.has(c.session) ? "sssf" : "interactive")),
        by_project: rows(inside, (c) => projectOf(c.cwd)),
        by_model: rows(inside, (c) => c.model),
        unpriced_models: unpriced.toSorted(),
      };
    };
    return {
      sample,
      window: span(sample?.five_hour ?? null, "five_hour"),
      week: span(sample?.seven_day ?? null, "seven_day"),
      transcripts: this.transcripts,
    };
  }
}
