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
 * the login; SSSF never reads its token. OpenAI doesn't document this method,
 * and whether pi's sign-in draws on Codex's windows is unverified
 * (docs/research/usage-monitors.md).
 */
import type { ChatGptLimits, LimitWindow } from "../shared/types.ts";

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

/** Polls every 5 minutes while the dashboard runs; `latest` is the newest reading. */
export class CodexLimits {
  latest: ChatGptLimits | null = null;
  private busy = false;
  private last = 0;

  constructor(readonly enabled: boolean = ENABLED, private readonly read = readOnce) {}

  /** The newest reading, starting a fresh one in the background when it's due. */
  current(now = Date.now()): ChatGptLimits | null {
    if (this.enabled && !this.busy && now - this.last >= EVERY_MS) {
      this.busy = true;
      this.last = now;
      void this.read(now).then((r) => {
        // An error keeps the last good windows, so a hiccup doesn't blank the meters.
        this.latest = r.error && this.latest && !this.latest.error ? { ...this.latest, error: r.error } : r;
      }).finally(() => { this.busy = false; });
    }
    return this.latest;
  }
}
