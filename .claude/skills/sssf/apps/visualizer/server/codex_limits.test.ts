import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CodexLimits, parse, readOnce } from "./codex_limits.ts";

const NOW = Date.parse("2026-11-07T02:00:00Z");
const S = (iso: string) => Date.parse(iso) / 1000;
const LIMITS = {
  rateLimits: {
    limitId: "codex", planType: "plus", rateLimitReachedType: null,
    primary: { usedPercent: 12, windowDurationMins: 300, resetsAt: S("2026-11-07T05:00:00Z") },
    secondary: { usedPercent: 2, windowDurationMins: 10080, resetsAt: S("2026-11-12T00:00:00Z") },
  },
};
const ACCOUNT = { account: { type: "chatgpt", email: "someone@example.com", planType: "plus" }, requiresOpenaiAuth: true };

let dir: string;
beforeEach(() => { dir = mkdtempSync(join(tmpdir(), "codex-limits-")); });
afterEach(() => rmSync(dir, { recursive: true, force: true }));

/** A stand-in for `codex app-server` that logs each request and answers from `replies`. */
function fake(replies: Record<string, unknown>): string[] {
  const script = join(dir, "codex.ts");
  writeFileSync(script, `
    import { appendFileSync } from "node:fs";
    const replies = ${JSON.stringify(replies)};
    for await (const line of console) {
      appendFileSync(${JSON.stringify(join(dir, "sent.log"))}, line + "\\n");
      const m = JSON.parse(line);
      if (m.id === undefined) continue;
      const r = replies[m.method];
      console.log(JSON.stringify(r && r.error ? { id: m.id, error: r.error } : { id: m.id, result: r ?? {} }));
    }`);
  return [process.execPath, script];
}

describe("the ChatGPT plan's windows through Codex", () => {
  test("reads both windows and the plan, and keeps no email", () => {
    const got = parse(ACCOUNT, LIMITS, NOW);
    expect(got).toEqual({
      recorded_at: new Date(NOW).toISOString(), plan: "plus", signed_in: true, reached: null, error: null,
      five_hour: { utilization: 0.12, resets_at: "2026-11-07T05:00:00.000Z" },
      seven_day: { utilization: 0.02, resets_at: "2026-11-12T00:00:00.000Z" },
    });
    expect(JSON.stringify(got)).not.toContain("example.com");
  });

  test("places a window by its length, and drops one that has reset", () => {
    const got = parse(ACCOUNT, { rateLimits: {
      primary: { usedPercent: 40, windowDurationMins: 10080, resetsAt: S("2026-11-10T00:00:00Z") },
      secondary: { usedPercent: 90, windowDurationMins: 300, resetsAt: S("2026-11-07T01:00:00Z") },
    } }, NOW);
    expect(got.seven_day?.utilization).toBe(0.4);
    expect(got.five_hour).toBeNull();
  });

  test("sends only initialize, account/read and account/rateLimits/read", async () => {
    const got = await readOnce(NOW, fake({ "account/read": ACCOUNT, "account/rateLimits/read": LIMITS }));
    expect(got.five_hour?.utilization).toBe(0.12);
    const sent = readFileSync(join(dir, "sent.log"), "utf8").trim().split("\n").map((l) => JSON.parse(l).method);
    expect(sent).toEqual(["initialize", "initialized", "account/read", "account/rateLimits/read"]);
  });

  test("an error reply or a missing codex reads as an error", async () => {
    const err = await readOnce(NOW, fake({ "account/read": ACCOUNT, "account/rateLimits/read": { error: { code: -1, message: "not signed in" } } }));
    expect(err.error).toContain("not signed in");
    const missing = await readOnce(NOW, [join(dir, "no-such-codex")]);
    expect(missing.error).toContain("isn't installed");
  });

  test("polls only when enabled, and an error keeps the last good windows", async () => {
    let n = 0;
    const good = parse(ACCOUNT, LIMITS, NOW);
    const reads = [good, { ...good, five_hour: null, seven_day: null, error: "Codex didn't answer in time" }];
    const off = new CodexLimits(false, async () => reads[n++]!, join(dir, "h.jsonl"));
    expect(off.current(NOW)).toBeNull();
    expect(n).toBe(0);
    const on = new CodexLimits(true, async () => reads[n++]!, join(dir, "h.jsonl"));
    on.current(NOW);
    await Bun.sleep(0);
    expect(on.latest?.five_hour?.utilization).toBe(0.12);
    on.current(NOW + 60_000);
    expect(n).toBe(1);
    on.current(NOW + 6 * 60_000);
    await Bun.sleep(0);
    expect(on.latest?.five_hour?.utilization).toBe(0.12);
    expect(on.latest?.error).toContain("didn't answer");
  });
});

describe("the Codex readings kept over time", () => {
  test("a good reading goes into the history, an unchanged one only hourly, and a failed one never", async () => {
    const file = join(dir, "codex_history.jsonl");
    const good = parse(ACCOUNT, LIMITS, NOW);
    const reads = [good, { ...good, recorded_at: new Date(NOW + 6 * 60_000).toISOString() },
      { ...good, five_hour: null, seven_day: null, error: "no" }];
    let n = 0;
    const limits = new CodexLimits(true, async () => reads[n++]!, file);
    for (const t of [NOW, NOW + 6 * 60_000, NOW + 12 * 60_000]) {
      limits.current(t);
      await Bun.sleep(0);
    }
    const lines = readFileSync(file, "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(lines).toEqual([{ at: good.recorded_at, w: { five_hour: [0.12, S("2026-11-07T05:00:00Z")], seven_day: [0.02, S("2026-11-12T00:00:00Z")] } }]);
    expect(new CodexLimits(true, async () => good, file).history).toHaveLength(1);
  });
});

describe("ChatGPT's closed weeks", () => {
  test("a reading with a new weekly reset archives the week that ended, once", async () => {
    const file = join(dir, "codex_history.jsonl");
    const end = S("2026-11-12T00:00:00Z");
    const before = { ...parse(ACCOUNT, LIMITS, NOW), recorded_at: "2026-11-11T23:00:00.000Z",
      seven_day: { utilization: 0.31, resets_at: "2026-11-12T00:00:00.000Z" }, five_hour: null };
    const after = (at: string) => ({ ...before, recorded_at: at,
      seven_day: { utilization: 0.01, resets_at: "2026-11-19T00:00:00.000Z" } });
    const reads = [before, after("2026-11-12T01:00:00.000Z"), after("2026-11-12T02:30:00.000Z")];
    let n = 0;
    const limits = new CodexLimits(true, async () => reads[n++]!, file);
    for (const t of [NOW, NOW + 6 * 60_000, NOW + 12 * 60_000]) {
      limits.current(t);
      await Bun.sleep(0);
    }
    const archived = readFileSync(join(dir, "window_archive.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
    expect(archived).toEqual([{ provider: "chatgpt", kind: "seven_day", start: end - 7 * 86_400, end, peak: 0.31,
      last_at: "2026-11-11T23:00:00.000Z" }]);
  });
});

describe("the timer", () => {
  test("reads at once and on each tick, with no page asking", async () => {
    let reads = 0;
    const limits = new CodexLimits(true, async () => { reads++; return parse(ACCOUNT, LIMITS, NOW); }, join(dir, "h.jsonl"));
    let ticks = 0;
    const current = limits.current.bind(limits);
    limits.current = (now?: number) => { ticks++; return current(now); };
    const timer = limits.start(5)!;
    await Bun.sleep(30);
    clearInterval(timer);
    expect(reads).toBe(1);            // the later ticks fall inside the 5-minute spacing
    expect(ticks).toBeGreaterThan(2);
    expect(limits.latest?.seven_day).not.toBeNull();
  });

  test("doesn't start when SSSF_CODEX_LIMITS is off", () => {
    let reads = 0;
    const limits = new CodexLimits(false, async () => { reads++; return parse(ACCOUNT, LIMITS, NOW); }, join(dir, "h.jsonl"));
    expect(limits.start(5)).toBeNull();
    expect(reads).toBe(0);
  });
});
