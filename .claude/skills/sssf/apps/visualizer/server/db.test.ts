import { afterEach, beforeEach, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SssfDb } from "./db.ts";

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-db-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function seed(): string {
  const path = join(dir, "sssf.db");
  const db = new Database(path);
  db.exec("PRAGMA journal_mode=WAL;");
  db.exec("CREATE TABLE agent_sessions (adw_id TEXT, agent TEXT, session_id TEXT, PRIMARY KEY (adw_id, agent))");
  db.exec("CREATE TABLE events (adw_id TEXT, type TEXT, name TEXT, payload_json TEXT)");
  // adw_tickets runs one builder session per ticket; agent_sessions keeps the last.
  for (const id of ["s-t01", "s-t02"]) {
    db.query("INSERT INTO events VALUES ('a1', 'agent_start', 'ticket_builder', ?)").run(
      JSON.stringify({ session_id: id }),
    );
  }
  db.exec("INSERT INTO agent_sessions VALUES ('a1', 'ticket_builder', 's-t02')");
  db.exec("INSERT INTO agent_sessions VALUES ('a0', 'planner', 's-old')");
  db.close();
  return path;
}

test("every agent session counts as SSSF's, not only each agent's latest", () => {
  const db = new SssfDb(seed());
  expect(db.agentSessionIds().toSorted()).toEqual(["s-old", "s-t01", "s-t02"]);
  db.close();
});

test("Pi calls carry the model their agent started with, and Claude Code calls are left out", () => {
  const path = join(dir, "pi.db");
  const db = new Database(path);
  db.exec("CREATE TABLE events (adw_id TEXT, type TEXT, name TEXT, payload_json TEXT, started_at TEXT)");
  const ev = db.query("INSERT INTO events VALUES (?, ?, ?, ?, ?)");
  ev.run("p1", "agent_start", "builder", JSON.stringify({ coding_agent: "pi", model: "openai/gpt-6.1-sol" }), "2026-10-29T10:00:00Z");
  ev.run("p1", "agent_end", "builder", JSON.stringify({ cost: 0.3, usage: { input_tokens: 50, cache_read_tokens: 950, output_tokens: 20 } }), "2026-10-29T10:05:00Z");
  ev.run("c1", "agent_start", "builder", JSON.stringify({ coding_agent: "claude_code", model: "anthropic/claude-sonnet-5-5" }), "2026-10-29T11:00:00Z");
  ev.run("c1", "agent_end", "builder", JSON.stringify({ cost: 1.2, usage: {} }), "2026-10-29T11:05:00Z");
  ev.run("p1", "agent_end", "builder", JSON.stringify({ cost: 5 }), "2026-10-01T10:05:00Z");
  db.close();
  const sssf = new SssfDb(path);
  expect(sssf.piCalls("2026-10-20T00:00:00Z")).toEqual([
    { at: "2026-10-29T10:05:00Z", model: "openai/gpt-6.1-sol", cost: 0.3, input_tokens: 1000, output_tokens: 20 },
  ]);
  sssf.close();
});

test("the latest ChatGPT plan-limit stop is found, and Claude's usage stops are not", () => {
  const path = join(dir, "limit.db");
  const db = new Database(path);
  db.exec("CREATE TABLE events (adw_id TEXT, type TEXT, name TEXT, payload_json TEXT, started_at TEXT)");
  const ev = db.query("INSERT INTO events VALUES (?, 'error', 'rate_limit_stop', ?, ?)");
  ev.run("old1", JSON.stringify({ provider: "openai" }), "2026-10-01T10:00:00Z");
  ev.run("p1", JSON.stringify({ provider: "openai" }), "2026-10-29T10:00:00Z");
  ev.run("p2", JSON.stringify({ provider: "openai" }), "2026-10-30T10:00:00Z");
  ev.run("c1", JSON.stringify({ error: "Claude's 5-hour window is at 85%" }), "2026-10-31T10:00:00Z");
  db.close();
  const sssf = new SssfDb(path);
  expect(sssf.piLimitHit("2026-10-20T00:00:00Z")).toEqual({ at: "2026-10-30T10:00:00Z", adw_id: "p2" });
  expect(sssf.piLimitHit("2026-10-31T00:00:00Z")).toBeNull();
  sssf.close();
});
