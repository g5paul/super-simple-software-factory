import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Account, estimateCost, piSpend } from "./account.ts";

const NOW = Date.parse("2026-10-29T16:00:00Z");
const RESET_5H = Date.parse("2026-10-29T17:30:00Z") / 1000;
const RESET_7D = Date.parse("2026-11-01T12:00:00Z") / 1000;

let dir: string;
let transcripts: string;
let ratesFile: string;

function call(opts: { id: string; at: string; session: string; cwd: string; model?: string; out?: number; read?: number }) {
  return JSON.stringify({
    type: "assistant", timestamp: opts.at, sessionId: opts.session, cwd: opts.cwd, requestId: `req_${opts.id}`,
    message: { id: `msg_${opts.id}`, model: opts.model ?? "claude-opus-5-5",
      usage: { input_tokens: 10, output_tokens: opts.out ?? 100, cache_read_input_tokens: opts.read ?? 1000,
        cache_creation_input_tokens: 0, cache_creation: { ephemeral_5m_input_tokens: 0, ephemeral_1h_input_tokens: 0 } } },
  }) + "\n";
}

function sample(fiveHour: number, reset5h = RESET_5H) {
  writeFileSync(ratesFile, JSON.stringify({
    recorded_at: "2026-10-29T15:50:00Z", source: "/repos/yt",
    info: { status: "allowed", overageStatus: "rejected",
      unifiedWindows: { five_hour: { utilization: fiveHour, resetsAt: reset5h }, seven_day: { utilization: 0.05, resetsAt: RESET_7D } } },
  }));
}

const projectOf = (cwd: string) => cwd.split("/").at(-1) ?? cwd;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-account-"));
  transcripts = join(dir, "projects");
  mkdirSync(join(transcripts, "-repos-yt"), { recursive: true });
  ratesFile = join(dir, "rate_limits.json");
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

describe("Account", () => {
  test("splits SSSF agents from interactive sessions, by project and model", () => {
    const f = join(transcripts, "-repos-yt", "a.jsonl");
    writeFileSync(f, call({ id: "1", at: "2026-10-29T15:00:00Z", session: "human", cwd: "/repos/yt" })
      + call({ id: "2", at: "2026-10-29T15:10:00Z", session: "agent", cwd: "/repos/yt", model: "claude-haiku-5-5" }));
    sample(0.09);
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(["agent"]), projectOf);
    expect(usage.window.calls).toBe(2);
    expect(usage.window.by_kind.map((r) => [r.key, r.calls])).toEqual([["interactive", 1], ["sssf", 1]]);
    expect(usage.window.by_project.map((r) => r.key)).toEqual(["yt"]);
    expect(usage.window.by_model.map((r) => r.key).toSorted()).toEqual(["claude-haiku-5-5", "claude-opus-5-5"]);
  });

  test("counts a streamed message once, though it repeats per content block", () => {
    const line = call({ id: "1", at: "2026-10-29T15:00:00Z", session: "s", cwd: "/repos/yt" });
    writeFileSync(join(transcripts, "-repos-yt", "a.jsonl"), line + line + line);
    expect(new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf).window.calls).toBe(1);
  });

  test("the window is the live 5-hour window from the sample", () => {
    writeFileSync(join(transcripts, "-repos-yt", "a.jsonl"),
      call({ id: "old", at: "2026-10-29T12:00:00Z", session: "s", cwd: "/repos/yt" })
      + call({ id: "new", at: "2026-10-29T13:00:00Z", session: "s", cwd: "/repos/yt" }));
    sample(0.09);  // resets 17:30, so the window opened 12:30
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf);
    expect(usage.window.basis).toBe("five_hour");
    expect(usage.window.start).toBe("2026-10-29T12:30:00.000Z");
    expect(usage.window.calls).toBe(1);
    expect(usage.week.calls).toBe(2);
    expect(usage.sample?.five_hour?.utilization).toBe(0.09);
    expect(usage.sample?.age_s).toBe(600);
  });

  test("without a live sample, the window is the last five hours", () => {
    writeFileSync(join(transcripts, "-repos-yt", "a.jsonl"),
      call({ id: "1", at: "2026-10-29T10:00:00Z", session: "s", cwd: "/repos/yt" })
      + call({ id: "2", at: "2026-10-29T12:00:00Z", session: "s", cwd: "/repos/yt" }));
    sample(0.5, Date.parse("2026-10-29T11:00:00Z") / 1000);  // that window already reset
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf);
    expect(usage.window.basis).toBe("rolling");
    expect(usage.window.calls).toBe(1);
    expect(usage.sample?.five_hour).toBeNull();
    expect(usage.sample?.has_five_hour).toBe(true);      // reset, but the plan has one
  });

  test("an account with no 5-hour window says so, as Ultra's does", () => {
    writeFileSync(ratesFile, JSON.stringify({
      recorded_at: "2026-10-29T15:50:00Z", source: "/repos/yt",
      info: { status: "allowed", unifiedWindows: { seven_day: { utilization: 0.07, resetsAt: RESET_7D } } },
    }));
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf);
    expect(usage.sample?.has_five_hour).toBe(false);
    expect(usage.sample?.seven_day?.utilization).toBe(0.07);
  });

  test("a model the price table doesn't list is named, in the window and the week", () => {
    writeFileSync(join(transcripts, "-repos-yt", "a.jsonl"),
      call({ id: "1", at: "2026-10-29T15:00:00Z", session: "s", cwd: "/repos/yt", model: "claude-mystery-9" })
      + call({ id: "2", at: "2026-10-29T15:10:00Z", session: "s", cwd: "/repos/yt" }));
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf);
    expect(usage.window.unpriced_models).toEqual(["claude-mystery-9"]);
    expect(usage.week.unpriced_models).toEqual(["claude-mystery-9"]);
  });

  test("no sample file still reports spend", () => {
    writeFileSync(join(transcripts, "-repos-yt", "a.jsonl"), call({ id: "1", at: "2026-10-29T15:00:00Z", session: "s", cwd: "/repos/yt" }));
    const usage = new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf);
    expect(usage.sample).toBeNull();
    expect(usage.window.calls).toBe(1);
  });

  test("reads only what a growing transcript appended, and waits for a partial line", () => {
    const f = join(transcripts, "-repos-yt", "a.jsonl");
    writeFileSync(f, call({ id: "1", at: "2026-10-29T15:00:00Z", session: "s", cwd: "/repos/yt" }));
    const account = new Account(transcripts, ratesFile);
    expect(account.summary(NOW, new Set(), projectOf).window.calls).toBe(1);
    const next = call({ id: "2", at: "2026-10-29T15:30:00Z", session: "s", cwd: "/repos/yt" });
    appendFileSync(f, next.slice(0, 40));
    expect(account.summary(NOW, new Set(), projectOf).window.calls).toBe(1);
    appendFileSync(f, next.slice(40));
    expect(account.summary(NOW, new Set(), projectOf).window.calls).toBe(2);
  });

  test("finds subagent transcripts in nested folders", () => {
    mkdirSync(join(transcripts, "-repos-yt", "sess", "subagents"), { recursive: true });
    writeFileSync(join(transcripts, "-repos-yt", "sess", "subagents", "agent-1.jsonl"),
      call({ id: "1", at: "2026-10-29T15:00:00Z", session: "s", cwd: "/repos/yt" }));
    expect(new Account(transcripts, ratesFile).summary(NOW, new Set(), projectOf).window.calls).toBe(1);
  });
});

describe("estimateCost", () => {
  const none = { input: 0, output: 0, cacheRead: 0, cacheWrite5m: 0, cacheWrite1h: 0 };

  test("prices each token kind at claude.com/pricing's list price", () => {
    expect(estimateCost("claude-opus-5-5", { ...none, input: 1_000_000 })).toBeCloseTo(4);
    expect(estimateCost("claude-opus-5-5", { ...none, output: 1_000_000 })).toBeCloseTo(20);
    expect(estimateCost("claude-opus-5-5", { ...none, cacheRead: 1_000_000 })).toBeCloseTo(0.2);
    expect(estimateCost("claude-sonnet-5-5", { ...none, cacheWrite5m: 1_000_000 })).toBeCloseTo(2.5);
    expect(estimateCost("claude-fable-5-1", { ...none, output: 1_000_000 })).toBeCloseTo(50);
  });

  test("a long Haiku prompt costs five times as much", () => {
    expect(estimateCost("claude-haiku-5-5", { ...none, input: 100_000 })).toBeCloseTo(0.01);
    expect(estimateCost("claude-haiku-5-5", { ...none, input: 200_000 })).toBeCloseTo(0.1);
  });

  test("an unknown model has no estimate", () => {
    expect(estimateCost("claude-mystery-9", { input: 1, output: 1, cacheRead: 0, cacheWrite5m: 0, cacheWrite1h: 0 })).toBeNull();
  });
});

describe("Pi agents' spend", () => {
  const pi = (at: string, project: string, model: string, cost: number) =>
    ({ at: Date.parse(at), project, model, cost, input_tokens: 1000, output_tokens: 100 });

  test("adds up the last 5 hours and 7 days by project and model", () => {
    const spend = piSpend([
      pi("2026-10-29T15:00:00Z", "yt", "openai/gpt-6.1-sol", 0.4),
      pi("2026-10-29T14:00:00Z", "sandbox", "openai/gpt-6.1-sol", 0.1),
      pi("2026-10-27T09:00:00Z", "yt", "google/gemini-3.6-flash", 0.05),
      pi("2026-10-20T09:00:00Z", "yt", "openai/gpt-6.1-sol", 9),
    ], NOW);
    expect(spend.window.calls).toBe(2);
    expect(spend.window.est_usd).toBeCloseTo(0.5);
    expect(spend.window.by_project.map((r) => r.key)).toEqual(["yt", "sandbox"]);
    expect(spend.week.calls).toBe(3);
    expect(spend.week.by_model.map((r) => [r.key, Number(r.est_usd.toFixed(2))])).toEqual([
      ["openai/gpt-6.1-sol", 0.5], ["google/gemini-3.6-flash", 0.05],
    ]);
  });

  test("no Pi calls means empty windows, not an error", () => {
    const spend = piSpend([], NOW);
    expect([spend.window.calls, spend.week.calls]).toEqual([0, 0]);
  });
});
