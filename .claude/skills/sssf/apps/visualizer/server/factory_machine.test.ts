import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { GIB } from "./factory_limits.ts";
import {
  leftoverRow, memoryRow, pollRows, pruneRows, readMeminfo, readPruneStatus, scanProcesses, vmRows,
} from "./factory_machine.ts";

const NOW = Date.parse("2026-11-01T12:00:00Z");
const hoursAgo = (h: number) => new Date(NOW - h * 3600_000).toISOString();

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-fmachine-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

function meminfo(kb: number | null) {
  writeFileSync(join(dir, "meminfo"), kb === null ? "MemTotal: 100 kB\n" : `MemTotal: 99999999 kB\nMemAvailable:   ${kb} kB\n`);
}

function proc(pid: number, o: { uid?: number; argv: string[]; cwd?: string; start?: number }) {
  const d = join(dir, String(pid));
  mkdirSync(d);
  writeFileSync(join(d, "status"), `Name:\tx\nUid:\t${o.uid ?? 1000}\t${o.uid ?? 1000}\t0\t0\n`);
  writeFileSync(join(d, "cmdline"), o.argv.join("\0") + "\0");
  const fields = Array.from({ length: 30 }, () => "0");
  fields[0] = "S";
  fields[19] = String(o.start ?? 0);   // field 22 overall is index 19 after the ") "
  writeFileSync(join(d, "stat"), `${pid} (we ird) ${fields.join(" ")}`);
  if (o.cwd) symlinkSync(o.cwd, join(d, "cwd"));
}

test("memory: 4 GiB ok, 2 GiB amber, 1 GiB red, unreadable amber", () => {
  meminfo(4 * 1024 * 1024);
  expect(memoryRow(readMeminfo(dir).available).level).toBe("ok");
  meminfo(2 * 1024 * 1024);
  expect(memoryRow(readMeminfo(dir).available).level).toBe("amber");
  meminfo(1024 * 1024);
  const red = memoryRow(readMeminfo(dir).available);
  expect(red.level).toBe("red");
  expect(red.figure).toBe("1.0 GiB free");
  expect(memoryRow(null).level).toBe("amber");
  rmSync(join(dir, "meminfo"));
  expect(readMeminfo(dir).available).toBeNull();
  meminfo(null);
  expect(readMeminfo(dir).available).toBeNull();
});

test("leftovers: cwd or argv under pytest-of-user is red, with pids and ages", () => {
  const tmp = join(dir, "tmpdir");
  const pyDir = join(tmp, "pytest-of-gp", "pytest-1");
  mkdirSync(pyDir, { recursive: true });
  writeFileSync(join(dir, "uptime"), "5000.00 100.00\n");
  proc(11, { argv: ["sleep", "99"], cwd: pyDir, start: 100_000 });                 // started at 1000 s
  proc(12, { argv: ["tail", "/tmp/pytest-of-gp/x"], cwd: dir, start: 400_000 });   // started at 4000 s
  proc(13, { argv: ["sleep", "1"], cwd: pyDir, uid: 0 });                          // someone else's
  proc(14, { argv: ["sleep", "1"], cwd: pyDir });                                  // self
  mkdirSync(join(dir, "15"));                                                      // unreadable entry
  const procs = scanProcesses(dir, { uid: 1000, selfPid: 14 });
  expect(procs.map((p) => p.pid).toSorted()).toEqual([11, 12]);
  const row = leftoverRow(procs, { tmpdir: tmp, user: "gp" });
  expect(row.level).toBe("red");
  expect(row.figure).toBe("2 processes");
  const byPid = Object.fromEntries((row.processes ?? []).map((p) => [p.pid, p]));
  expect(byPid[11].age_s).toBeCloseTo(4000);
  expect(byPid[12].age_s).toBeCloseTo(1000);
  expect(row.next).toContain("11,12");
});

test("leftovers: a running pytest makes the row ok, and no leftovers is ok", () => {
  const tmp = join(dir, "tmpdir");
  const pyDir = join(tmp, "pytest-of-gp");
  mkdirSync(pyDir, { recursive: true });
  proc(21, { argv: ["python", "-m", "pytest", "-q"], cwd: dir });
  proc(22, { argv: ["sleep", "1"], cwd: pyDir });
  const busy = leftoverRow(scanProcesses(dir, { uid: 1000, selfPid: 1 }), { tmpdir: tmp, user: "gp" });
  expect(busy.level).toBe("ok");
  expect(busy.notes.join()).toContain("in progress");
  const none = leftoverRow([], { tmpdir: tmp, user: "gp" });
  expect(none.level).toBe("ok");
  expect(none.processes).toBeUndefined();
});

test("the agentsh store: 10 GiB ok, 16 amber, 26 red, both null amber", () => {
  const level = (store: number | null, audit: number | null) =>
    pruneRows({ store_bytes: store, audit_bytes: audit, last_prune: hoursAgo(1), last_analyze: null, last_run: null }, NOW)[0].level;
  expect(level(8 * GIB, 2 * GIB)).toBe("ok");
  expect(level(10 * GIB, 6 * GIB)).toBe("amber");
  expect(level(20 * GIB, 6 * GIB)).toBe("red");
  expect(level(null, null)).toBe("amber");
});

test("the agentsh store's next step is config.md's: move events.db aside", () => {
  const row = pruneRows({ store_bytes: 10 * GIB, audit_bytes: 6 * GIB, last_prune: hoursAgo(1), last_analyze: null, last_run: null }, NOW)[0];
  expect(row.level).toBe("amber");
  expect(row.next).toContain("stop agentsh");
  expect(row.next).toContain("/var/lib/agentsh/events.db");
  expect(row.next).toContain("-wal and -shm");
  expect(row.next).not.toContain("hub session");
});

test("the prune: 1 h ok, 4 h amber, 7 h red, never amber, no file amber for both rows", () => {
  const level = (last: string | null) =>
    pruneRows({ store_bytes: 1, audit_bytes: 1, last_prune: last, last_analyze: null, last_run: null }, NOW)[1].level;
  expect(level(hoursAgo(1))).toBe("ok");
  expect(level(hoursAgo(4))).toBe("amber");
  expect(level(hoursAgo(7))).toBe("red");
  expect(level(null)).toBe("amber");
  const path = join(dir, "status.json");
  expect(readPruneStatus(path)).toBeNull();
  const rows = pruneRows(readPruneStatus(path), NOW, path);
  expect(rows.map((r) => r.level)).toEqual(["amber", "amber"]);
  expect(rows[0].problems[0]).toContain(path);
  expect(rows[0].next).toContain("systemctl enable");
  writeFileSync(path, JSON.stringify({ store_bytes: null, audit_bytes: 5, last_prune: hoursAgo(1) }));
  expect(readPruneStatus(path)?.audit_bytes).toBe(5);
  writeFileSync(path, "not json");
  expect(readPruneStatus(path)).toBeNull();
});

const polls = (...payloads: (Record<string, unknown> | null)[]) =>
  pollRows([{ repo: "r", events: payloads.map((payload) => ({ at: hoursAgo(1), payload })) }]);

test("polls: 0.5 s ok, 1.5 s amber, 3.5 s red", () => {
  expect(polls({ slowest_poll_seconds: 0.5, drops: {} })[0].level).toBe("ok");
  expect(polls({ slowest_poll_seconds: 1.5, drops: {} })[0].level).toBe("amber");
  expect(polls({ slowest_poll_seconds: 3.5, drops: {} })[0].level).toBe("red");
  expect(pollRows([{ repo: "r", events: [] }])).toEqual([]);
});

test("polls: ptrace_execve drops are amber, empty drops ok, unrecorded drops get a note", () => {
  const dropped = polls({ slowest_poll_seconds: 0.2, drops: { ptrace_execve: 2 } })[0];
  expect(dropped.level).toBe("amber");
  expect(dropped.figure).toBe("2 ptrace_execve dropped");
  const empty = polls({ slowest_poll_seconds: 0.2, drops: {} })[0];
  expect(empty.level).toBe("ok");
  expect(empty.notes).toEqual([]);
  const unknown = polls({ slowest_poll_seconds: 0.2, drops: null }, { slowest_poll_seconds: 0.1 })[0];
  expect(unknown.level).toBe("ok");
  expect(unknown.notes).toContain("drops: not recorded");
  const other = polls({ slowest_poll_seconds: 0.2, drops: { other: 3 } })[0];
  expect(other.level).toBe("ok");
  expect(other.notes[0]).toContain("other 3");
});

test("VMs: open with a running run is ok, open with a failed run is amber", () => {
  const rows = vmRows([{ repo: "r", open: [
    { adw_id: "a1", vm: "vm-1", provider: "daytona", at: hoursAgo(1), run_status: "running" },
    { adw_id: "a2", vm: "vm-2", provider: "daytona", at: hoursAgo(2), run_status: "fail" },
  ] }]);
  expect(rows.map((r) => r.level)).toEqual(["ok", "amber"]);
  expect(rows[0].notes).toContain("in use by a running run");
  expect(rows[1].problems[0]).toBe("VM vm-2 on daytona has no running SSSF run");
  expect(rows[1].next).toContain("run a2");
});
