import { afterEach, beforeEach, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SssfDb } from "./db.ts";
import { parsePhaseTimes, runRows, type FactoryRunFacts, type PhaseTimesJson, type SlowItem } from "./factory_runs.ts";

const NOW = Date.parse("2026-11-01T12:00:00Z");
const ago = (h: number) => new Date(NOW - h * 3600_000).toISOString().replace("Z", "+00:00");
const SINCE = ago(24);

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-fruns-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function seed(opts: { processes?: boolean } = {}): SssfDb {
  const path = join(dir, "sssf.db");
  const db = new Database(path);
  db.exec("PRAGMA journal_mode=WAL;");
  db.exec("CREATE TABLE sessions (adw_id TEXT, adw_name TEXT, status TEXT, started_at TEXT, ended_at TEXT, total_cost REAL, archived INTEGER DEFAULT 0)");
  db.exec("CREATE TABLE events (adw_id TEXT, phase_id TEXT, type TEXT, name TEXT, payload_json TEXT, started_at TEXT)");
  db.exec("CREATE TABLE envelopes (adw_id TEXT, output_type TEXT, payload_json TEXT, created_at TEXT)");
  if (opts.processes !== false) db.exec("CREATE TABLE processes (adw_id TEXT, kind TEXT, pid INTEGER, host TEXT, started_at TEXT, ended_at TEXT)");
  const session = (id: string, status: string, started: number, archived = 0) =>
    db.query("INSERT INTO sessions VALUES (?, 'adw_x', ?, ?, ?, 0, ?)").run(id, status, ago(started), status === "running" ? null : ago(started - 0.1), archived);
  const event = (id: string, type: string, name: string, payload: unknown, h = 1) =>
    db.query("INSERT INTO events VALUES (?, 'p', ?, ?, ?, ?)").run(id, type, name, JSON.stringify(payload), ago(h));
  session("ok1", "success", 2);
  session("fail1", "fail", 3);
  event("fail1", "error", "rule_breach", { message: "x" });
  session("crash1", "running", 4);
  session("live1", "running", 4);
  if (opts.processes !== false) {
    db.query("INSERT INTO processes VALUES ('live1', 'adw', 4242, NULL, ?, NULL)").run(ago(4));
    db.query("INSERT INTO processes VALUES ('crash1', 'adw', 4243, NULL, ?, ?)").run(ago(4), ago(3));
  }
  session("guard1", "success", 5);
  event("guard1", "log", "bypass_review_1", { bypasses: 0, harms: 0, false_positives: 0, broken_promises: 0, old_holes: 0, blocking: 1, adds_mechanism: false });
  session("guard2", "success", 5);
  event("guard2", "log", "bypass_review_1", { bypasses: 1, harms: 0, false_positives: 0, broken_promises: 0, old_holes: 0, blocking: 0, adds_mechanism: false });
  session("msg1", "success", 5);
  event("msg1", "log", "bypass_review_1", { message: "starting", level: "info" });
  session("ref1", "success", 5);
  event("ref1", "log", "rule_refused", { agent: "a", rule: "r", pattern: "p", command: "c" });
  session("spec1", "success", 5);
  db.query("INSERT INTO envelopes VALUES ('spec1', 'ReviewOutput', ?, ?)").run(JSON.stringify({ approved: false }), ago(5));
  session("old1", "success", 30);
  session("arch1", "success", 2, 1);
  event("ok1", "agent_end", "x", { cost: 1 });
  event("ok1", "log", "agentsh_denials", { slowest_poll_seconds: 2 }, 2);
  event("ok1", "sandbox", "sandbox", { provider: "daytona", vm: "vm-open" }, 3);
  event("ok1", "sandbox", "sandbox", { provider: "daytona", vm: "vm-done" }, 3);
  event("ok1", "sandbox", "sandbox_end", { provider: "daytona", vm: "vm-done" }, 2);
  db.close();
  return new SssfDb(path);
}

function facts(db: SssfDb): FactoryRunFacts[] {
  return db.factoryRuns(SINCE).map((r) => ({
    adw_id: r.adw_id, adw_name: r.adw_name ?? "", status: r.status, started_at: r.started_at, ended_at: r.ended_at,
    last_error: r.last_error,
    live: r.has_processes === 0 ? null : r.open_pid === null ? false : r.open_pid === 4242,
    refusals: r.refusals,
    guard: r.blocking === null ? null : { bypasses: r.bypasses ?? 0, blocking: r.blocking },
    spec_blocked: r.blocked_reviews > 0,
  }));
}

const slow = (...items: SlowItem[]): PhaseTimesJson => ({ repos: [{ repo: "r", slow: items }] });
const rows = (db: SssfDb, s: PhaseTimesJson = slow()) => runRows([{ repo: "r", runs: facts(db) }], s);
const find = (rs: ReturnType<typeof rows>, id: string) => rs.find((r) => r.adw_id === id)!;

test("red: a failed run with its breach, a crash, guard findings", () => {
  const rs = rows(seed());
  expect(find(rs, "fail1").level).toBe("red");
  expect(find(rs, "fail1").problems[0]).toBe("failed (rule_breach)");
  expect(find(rs, "crash1").level).toBe("red");
  expect(find(rs, "crash1").problems[0]).toContain("crashed");
  expect(find(rs, "live1").level).toBe("ok");
  expect(find(rs, "guard1").level).toBe("red");
  expect(find(rs, "guard1").guard).toEqual({ bypasses: 0, blocking: 1 });
  expect(find(rs, "guard2").level).toBe("red");
});

test("red and amber by phase and run ratio, amber by cost ratio", () => {
  const db = seed();
  const phase = (id: string, ratio: number): SlowItem => ({ kind: "phase", adw_id: id, name: "build", ratio });
  expect(find(rows(db, slow(phase("ok1", 3.2))), "ok1").level).toBe("red");
  expect(find(rows(db, slow(phase("ok1", 2.4))), "ok1").level).toBe("amber");
  expect(find(rows(db, slow({ kind: "run", adw_id: "ok1", ratio: 3.0 })), "ok1").level).toBe("red");
  const cost = find(rows(db, slow({ kind: "cost", adw_id: "ok1", ratio: 2.5, cost: 5, p90: 2 })), "ok1");
  expect(cost.level).toBe("amber");
  expect(cost.problems[0]).toContain("cost $5.00");
  expect(find(rows(db, slow({ kind: "cost", adw_id: "ok1", ratio: 1.5, cost: 3 })), "ok1").level).toBe("ok");
});

test("amber: a rule refusal, a blocked spec review; ok: a clean success", () => {
  const rs = rows(seed());
  expect(find(rs, "ref1").level).toBe("amber");
  expect(find(rs, "ref1").refusals).toBe(1);
  expect(find(rs, "spec1").level).toBe("amber");
  expect(find(rs, "ok1").level).toBe("ok");
  expect(find(rs, "ok1").problems).toEqual([]);
});

test("a message-only bypass_review event is ignored", () => {
  const msg = find(rows(seed()), "msg1");
  expect(msg.level).toBe("ok");
  expect(msg.guard).toBeNull();
});

test("archived runs and runs older than 24 h are left out", () => {
  const ids = rows(seed()).map((r) => r.adw_id);
  expect(ids).not.toContain("arch1");
  expect(ids).not.toContain("old1");
});

test("a trace without a processes table doesn't judge a crash", () => {
  const rs = rows(seed({ processes: false }));
  expect(find(rs, "crash1").level).toBe("ok");
});

test("polls and open VMs come from the trace", () => {
  const db = seed();
  expect(db.agentshPolls(SINCE).length).toBe(1);
  expect(db.openSandboxes().map((v) => v.vm)).toEqual(["vm-open"]);
  expect(db.openSandboxes()[0].run_status).toBe("success");
});

test("parsePhaseTimes wants JSON with a repos array", () => {
  expect(parsePhaseTimes('{"repos":[]}').repos).toEqual([]);
  expect(() => parsePhaseTimes("nope")).toThrow();
});
