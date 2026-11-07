/**
 * The ChatGPT plan's usage windows, as Codex CLI reads them. Opt-in: only when
 * the dashboard starts with SSSF_CODEX_LIMITS=1.
 *
 * pi's ChatGPT sign-in gets no usage windows back (docs/research/
 * pi-usage-limits.md), but Codex's app server answers `account/rateLimits/read`
 * with the plan's 5-hour and weekly windows, the call Codex's own /status
 * makes. SSSF starts `codex app-server` read-only, sends initialize,
 * account/read and account/rateLimits/read, and nothing else: the same
 * protocol can spend a reset credit, and SSSF never asks it to. Codex holds
 * the login; SSSF never reads its token. OpenAI doesn't document this method.
 * pi's ChatGPT use draws on the same windows: a test run moved them
 * (docs/research/usage-monitors.md).
 */
import { appendFileSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import type { ChatGptLimits, LimitWindow } from "../shared/types.ts";
import { registryPath } from "./projects.ts";
import { readHistory, type Reading } from "./window_use.ts";

export const ENABLED = process.env.SSSF_CODEX_LIMITS === "1";
const EVERY_MS = 5 * 60_000;
const TIMEOUT_MS = 30_000;

/** The only requests SSSF sends. */
export const REQUESTS = [
  { id: 1, method: "initialize", params: { clientInfo: { name: "sssf-dashboard", version: "1" } } },
  { method: "initialized" },
  { id: 2, method: "account/read", params: {} },
  { id: 3, method: "account/rateLimits/read" },
] as const;

interface Window { usedPercent?: number; windowDurationMins?: number | null; resetsAt?: number | null }

/** Codex labels a window by its length, as its /status does: about 300 minutes is 5h, 10080 weekly. */
function slot(w: Window): "five_hour" | "seven_day" | null {
  const mins = w.windowDurationMins ?? 0;
  if (mins >= 240 && mins <= 360) return "five_hour";
  if (mins >= 9000 && mins <= 11000) return "seven_day";
  return null;
}

/** The replies to account/read and account/rateLimits/read, as the panel shows them. */
export function parse(account: unknown, limits: unknown, at: number): ChatGptLimits {
  const acct = (account as { account?: { type?: string; planType?: string } })?.account ?? null;
  const snap = (limits as { rateLimits?: Record<string, unknown> })?.rateLimits ?? {};
  const out: ChatGptLimits = {
    recorded_at: new Date(at).toISOString(),
    plan: (snap.planType as string) ?? acct?.planType ?? null,
    signed_in: acct?.type === "chatgpt",
    five_hour: null,
    seven_day: null,
    reached: (snap.rateLimitReachedType as string) ?? null,
    error: null,
  };
  for (const key of ["primary", "secondary"]) {
    const w = snap[key] as Window | null | undefined;
    if (!w || typeof w.usedPercent !== "number" || !w.resetsAt) continue;
    const s = slot(w);
    const live: LimitWindow = { utilization: w.usedPercent / 100, resets_at: new Date(w.resetsAt * 1000).toISOString() };
    if (s && w.resetsAt * 1000 > at) out[s] = live;
  }
  return out;
}

/** Ask Codex once. Resolves with what it said, or an error reading. */
export async function readOnce(now = Date.now(), command = ["codex", "-s", "read-only", "-a", "never", "app-server"]): Promise<ChatGptLimits> {
  const failed = (error: string): ChatGptLimits => ({
    recorded_at: new Date(now).toISOString(), plan: null, signed_in: false,
    five_hour: null, seven_day: null, reached: null, error,
  });
  let proc;
  try {
    proc = Bun.spawn(command, { stdin: "pipe", stdout: "pipe", stderr: "ignore" });
  } catch {
    return failed("Codex CLI isn't installed, so it can't read the plan's windows");
  }
  const timer = setTimeout(() => proc.kill(), TIMEOUT_MS);
  try {
    for (const m of REQUESTS) proc.stdin.write(`${JSON.stringify(m)}\n`);
    await proc.stdin.flush();
    const got = new Map<number, { result?: unknown; error?: { message?: string } }>();
    const decoder = new TextDecoder();
    let buffer = "";
    const reader = proc.stdout.getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        try {
          const m = JSON.parse(line);
          if (m.id === 2 || m.id === 3) got.set(m.id, m);
        } catch {
          // not a protocol line
        }
      }
      if (got.size === 2) break;
    }
    const limits = got.get(3);
    if (!limits) return failed("Codex didn't answer in time");
    if (limits.error) return failed(`Codex couldn't read the plan's windows: ${limits.error.message ?? "unknown error"}`);
    return parse(got.get(2)?.result, limits.result, now);
  } finally {
    clearTimeout(timer);
    proc.kill();
  }
}

export function historyPath(): string {
  return join(dirname(registryPath()), "codex_limits_history.jsonl");
}

const HISTORY_EVERY_MS = 3_600_000;
const HISTORY_KEEP_MS = 8 * 86_400_000;

/**
 * Polls every 5 minutes while the dashboard runs; `latest` is the newest reading.
 * Each good reading also goes into the history, in account.py's format, when a
 * window moved or the last line is an hour old.
 */
export class CodexLimits {
  latest: ChatGptLimits | null = null;
  history: Reading[];
  private busy = false;
  private last = 0;

  constructor(readonly enabled: boolean = ENABLED, private readonly read = readOnce, readonly historyFile: string = historyPath()) {
    this.history = enabled ? readHistory(historyFile) : [];
    if (this.history.length > 3000) {
      // Past this, keep 8 days: rewrite the file once at start.
      const cutoff = Date.now() - HISTORY_KEEP_MS;
      this.history = this.history.filter((h) => h.at >= cutoff);
      try {
        writeFileSync(historyFile, this.history.map((h) => `${JSON.stringify({ at: new Date(h.at).toISOString(), w: h.w })}\n`).join(""));
      } catch {
        // kept in memory either way
      }
    }
  }

  private remember(r: ChatGptLimits): void {
    const w: Reading["w"] = {};
    for (const k of ["five_hour", "seven_day"] as const) {
      const live = r[k];
      if (live) w[k] = [live.utilization, Math.round(Date.parse(live.resets_at) / 1000)];
    }
    if (!w.five_hour && !w.seven_day) return;
    const at = Date.parse(r.recorded_at);
    const prev = this.history.at(-1);
    // An idle 5-hour window's reset moves with every reading, so it alone isn't a change.
    const same = prev && JSON.stringify({ ...prev.w, five_hour: prev.w.five_hour?.[0] })
      === JSON.stringify({ ...w, five_hour: w.five_hour?.[0] });
    if (same && at - prev.at < HISTORY_EVERY_MS) return;
    this.history = [...this.history.filter((h) => h.at >= at - HISTORY_KEEP_MS), { at, w }];
    try {
      mkdirSync(dirname(this.historyFile), { recursive: true });
      appendFileSync(this.historyFile, `${JSON.stringify({ at: r.recorded_at, w })}\n`);
    } catch {
      // the meters still work from memory
    }
  }

  /** The newest reading, starting a fresh one in the background when it's due. */
  current(now = Date.now()): ChatGptLimits | null {
    if (this.enabled && !this.busy && now - this.last >= EVERY_MS) {
      this.busy = true;
      this.last = now;
      void this.read(now).then((r) => {
        // An error keeps the last good windows, so a hiccup doesn't blank the meters.
        this.latest = r.error && this.latest && !this.latest.error ? { ...this.latest, error: r.error } : r;
        if (!r.error) this.remember(r);
      }).finally(() => { this.busy = false; });
    }
    return this.latest;
  }
}
