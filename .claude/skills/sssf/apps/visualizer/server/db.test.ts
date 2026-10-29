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
