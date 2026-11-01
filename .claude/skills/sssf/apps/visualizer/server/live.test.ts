import { afterEach, beforeEach, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SssfDb } from "./db.ts";
import { liveSpend, pidAlive, readLiveCap } from "./live.ts";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-live-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const HERE = "box";
const LIVE = 101;
const alive = (pid: number) => pid === LIVE;

function trace(): SssfDb {
  const path = join(dir, "sssf.db");
  const db = new Database(path);
  db.exec("CREATE TABLE sessions (adw_id TEXT PRIMARY KEY, status TEXT)");
  db.exec("CREATE TABLE events (adw_id TEXT, type TEXT, payload_json TEXT, started_at TEXT)");
  db.exec(`CREATE TABLE processes (id INTEGER PRIMARY KEY, adw_id TEXT, kind TEXT, name TEXT, pid INTEGER,
           command TEXT, started_at TEXT, ended_at TEXT, host TEXT)`);
  const proc = db.query("INSERT INTO processes (adw_id, kind, pid, started_at, ended_at, host) VALUES (?, ?, ?, ?, ?, ?)");
  const call = db.query("INSERT INTO events VALUES (?, 'agent_end', ?, ?)");
  const cost = (c: number) => JSON.stringify({ cost: c });
  // live: a run whose pid is alive, on its second attempt. The first attempt's
  // process ended; its $0.90 is not this attempt's.
  db.exec("INSERT INTO sessions VALUES ('live', 'running')");
  proc.run("live", "adw", 90, "2026-11-01T09:00:00.000+00:00", "2026-11-01T09:30:00.000+00:00", HERE);
  call.run("live", cost(0.9), "2026-11-01T09:10:00.000+00:00");
  proc.run("live", "adw", LIVE, "2026-11-01T10:00:00.000+00:00", null, HERE);
  call.run("live", cost(0.25), "2026-11-01T10:05:00.000+00:00");
  call.run("live", cost(0.15), "2026-11-01T10:09:00.000+00:00");
  proc.run("live", "agent", 555, "2026-11-01T10:01:00.000+00:00", null, HERE);   // a child, not the run
  // dead: killed with -9, so its row never closed and its session still reads running.
  db.exec("INSERT INTO sessions VALUES ('dead', 'running')");
  proc.run("dead", "adw", 202, "2026-11-01T08:00:00.000+00:00", null, HERE);
  call.run("dead", cost(3), "2026-11-01T08:05:00.000+00:00");
  // away: another host's run; its pid means nothing here, so its status decides.
  db.exec("INSERT INTO sessions VALUES ('away', 'running')");
  proc.run("away", "adw", LIVE, "2026-11-01T10:00:00.000+00:00", null, "other");
  call.run("away", cost(0.5), "2026-11-01T10:02:00.000+00:00");
  db.close();
  return new SssfDb(path);
}

test("live runs count their current attempt; a dead pid and an ended attempt don't", () => {
  const db = trace();
  const live = liveSpend(db.liveRows(), 1.0, alive, HERE);
  db.close();
  expect(live.runs.map((r) => r.adw_id)).toEqual(["away", "live"]);
  expect(live.runs[1]!.cost).toBeCloseTo(0.4);
  expect(live.spent).toBeCloseTo(0.9);
  expect(live.cap).toBe(1.0);
});

test("another host's run counts only while its session reads running", () => {
  const rows = [{ adw_id: "away", pid: 7, host: "other", status: "success", cost: 2 }];
  expect(liveSpend(rows, null, () => true, HERE).runs).toEqual([]);
});

test("a row with no host is this host's, as the check has it", () => {
  const rows = [{ adw_id: "old", pid: LIVE, host: null, status: "success", cost: 0.3 }];
  expect(liveSpend(rows, null, alive, HERE).runs).toEqual([{ adw_id: "old", cost: 0.3 }]);
});

test("nothing live spends nothing", () => {
  expect(liveSpend([], 2, alive, HERE)).toEqual({ spent: 0, cap: 2, runs: [] });
});

test("a trace with no processes table has no live runs", () => {
  const path = join(dir, "bare.db");
  new Database(path).close();
  const db = new SssfDb(path);
  expect(db.liveRows()).toEqual([]);
  db.close();
});

test("this process is alive and a pid past the limit is not", () => {
  expect(pidAlive(process.pid)).toBe(true);
  expect(pidAlive(2 ** 22 + 12345)).toBe(false);
});

test("the cap is repo.yaml's max_live_cost, and nothing else", () => {
  mkdirSync(join(dir, "adws", "adw_sssf_config"), { recursive: true });
  const yaml = join(dir, "adws", "adw_sssf_config", "repo.yaml");
  writeFileSync(yaml, "max_live_cost: 6.5\nmax_daily_cost: 20\n");
  expect(readLiveCap(dir)).toBe(6.5);
  writeFileSync(yaml, "max_daily_cost: 20\n");
  expect(readLiveCap(dir)).toBeNull();
});
