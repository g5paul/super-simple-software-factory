import { afterEach, beforeEach, expect, test } from "bun:test";
import { Database } from "bun:sqlite";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Factory, factoryHandler, factoryHub, type Runner } from "./factory.ts";
import { Projects } from "./projects.ts";
import type { FactoryBoard } from "../shared/types.ts";

let dir: string;
let saved: string | undefined;
let now: number;
const NOW0 = Date.parse("2026-11-01T12:00:00Z");
const ago = (h: number) => new Date(NOW0 - h * 3600_000).toISOString().replace("Z", "+00:00");

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-factory-"));
  saved = process.env.SSSF_REGISTRY;
  now = NOW0;
});
afterEach(() => {
  if (saved === undefined) delete process.env.SSSF_REGISTRY;
  else process.env.SSSF_REGISTRY = saved;
  rmSync(dir, { recursive: true, force: true });
});

function seedRepo(name: string, runs: [string, string][]): string {
  const root = join(dir, "repos", name);
  mkdirSync(join(root, "adws", "adw_data"), { recursive: true });
  const db = new Database(join(root, "adws", "adw_data", "sssf.db"));
  db.exec("PRAGMA journal_mode=WAL;");
  db.exec("CREATE TABLE sessions (adw_id TEXT, adw_name TEXT, status TEXT, started_at TEXT, ended_at TEXT, total_cost REAL, archived INTEGER DEFAULT 0)");
  db.exec("CREATE TABLE events (adw_id TEXT, phase_id TEXT, type TEXT, name TEXT, payload_json TEXT, started_at TEXT)");
  db.exec("CREATE TABLE envelopes (adw_id TEXT, output_type TEXT, payload_json TEXT, created_at TEXT)");
  db.exec("CREATE TABLE processes (adw_id TEXT, kind TEXT, pid INTEGER, host TEXT, started_at TEXT, ended_at TEXT)");
  for (const [id, status] of runs) {
    db.query("INSERT INTO sessions VALUES (?, 'adw_x', ?, ?, ?, 0, 0)").run(id, status, ago(2), ago(1));
    db.query("INSERT INTO events VALUES (?, 'p', 'log', 'sandbox', ?, ?)").run(id, JSON.stringify({ provider: "daytona", vm: `vm-${id}` }), ago(2));
  }
  db.close();
  return root;
}

interface Fixture { factory: Factory; calls: Map<string, number>; commonDir: string; handler: (r: Request) => Promise<Response>; }

function fixture(opts: { names?: string[] | null; check?: string; checkCode?: number } = {}): Fixture {
  const alpha = seedRepo("alpha", [["a-ok", "success"], ["a-bad", "fail"]]);
  const beta = seedRepo("secret-beta", [["b-bad", "fail"]]);
  const registry = join(dir, "projects.json");
  writeFileSync(registry, JSON.stringify({ projects: { alpha: { root: alpha }, "secret-beta": { root: beta } } }));
  process.env.SSSF_REGISTRY = registry;

  const commonDir = join(dir, "hub-git");
  mkdirSync(commonDir);
  if (opts.names !== null) writeFileSync(join(commonDir, "sssf-private-names"), `# private\n\n${(opts.names ?? ["secret"]).join("\n")}\n`);

  const proc = join(dir, "proc");
  mkdirSync(proc);
  writeFileSync(join(proc, "meminfo"), "MemAvailable: 1048576 kB\n");   // 1 GiB: red
  writeFileSync(join(proc, "uptime"), "100.0 1.0\n");

  const prune = join(dir, "status.json");
  writeFileSync(prune, JSON.stringify({ store_bytes: 1, audit_bytes: 1, last_prune: new Date(NOW0 - 3600_000).toISOString() }));

  const check = opts.check ?? JSON.stringify({
    skill: "def", hidden: 0,
    repos: [
      { repo: "alpha", branch: "main", root: alpha, stamped: "def", behind: false, at_stamp: true, ok: true, checks: [{ name: "clean", ok: true, note: "" }] },
      { repo: "secret-beta", branch: "main", root: beta, stamped: "old", behind: true, at_stamp: true, ok: false, checks: [{ name: "clean", ok: true, note: "" }] },
    ],
  });
  const calls = new Map<string, number>();
  const runner: Runner = async (cmd) => {
    const key = cmd.join(" ");
    calls.set(key, (calls.get(key) ?? 0) + 1);
    if (key.endsWith("check_projects.py --json")) return { code: opts.checkCode ?? 0, stdout: check, stderr: "" };
    if (key.endsWith("phase_times.py --json")) return { code: 0, stdout: JSON.stringify({ repos: [] }), stderr: "" };
    if (key.includes("--git-common-dir")) return { code: 0, stdout: `${commonDir}\n`, stderr: "" };
    return { code: 1, stdout: "", stderr: "unexpected command" };
  };
  const factory = new Factory({
    hub: join(dir, "hub"), projects: new Projects(null), runner, procRoot: proc, pruneStatus: prune,
    uid: 1000, selfPid: 1, tmpdir: join(dir, "tmp"), user: "gp", hostname: "h", now: () => now,
  });
  return { factory, calls, commonDir, handler: factoryHandler(factory) };
}

const get = async (f: Fixture, query = ""): Promise<{ status: number; body: FactoryBoard & { error?: string } }> => {
  const res = await f.handler(new Request(`http://x/api/factory${query}`));
  return { status: res.status, body: (await res.json()) as FactoryBoard & { error?: string } };
};
const scriptRuns = (f: Fixture) => f.calls.get("uv run adws/check_projects.py --json") ?? 0;

test("rows come back red, then amber, then ok, with the counts", async () => {
  const { status, body } = await get(fixture());
  expect(status).toBe(200);
  const levels = body.rows.map((r) => r.level);
  expect(levels).toEqual(levels.toSorted((a, b) => ["red", "amber", "ok"].indexOf(a) - ["red", "amber", "ok"].indexOf(b)));
  expect(body.red).toBe(levels.filter((l) => l === "red").length);
  expect(body.amber).toBe(levels.filter((l) => l === "amber").length);
  const keys = body.rows.map((r) => r.key);
  expect(keys).toContain("machine/memory");
  expect(keys).toContain("runs/alpha/a-bad");
  expect(body.rows.find((r) => r.key === "machine/memory")?.level).toBe("red");
  expect(body.rows.find((r) => r.key === "projects/secret-beta")?.level).toBe("amber");
  expect(body.private_names).toBe(true);
  expect(body.hidden).toBe(0);
});

test("listed=1 hides a repo named in the private names, with its project, run and VM rows", async () => {
  const f = fixture();
  const all = (await get(f)).body;
  expect(all.rows.some((r) => r.repo === "secret-beta" && r.section === "machine")).toBe(true);
  const { body } = await get(f, "?listed=1");
  expect(body.hidden).toBe(1);
  expect(body.rows.some((r) => r.repo === "secret-beta")).toBe(false);
  expect(body.rows.some((r) => r.key === "machine/memory")).toBe(true);
  expect(body.rows.some((r) => r.repo === "alpha")).toBe(true);
});

test("listed=1 with no names file is refused with 409", async () => {
  const { status, body } = await get(fixture({ names: null }), "?listed=1");
  expect(status).toBe(409);
  expect(body.error).toContain("sssf-private-names");
});

test("scripts are cached for 30 s, refresh=1 reruns them, and so does time", async () => {
  const f = fixture();
  await get(f);
  expect(scriptRuns(f)).toBe(1);
  now += 10_000;
  await get(f);
  expect(scriptRuns(f)).toBe(1);
  await get(f, "?refresh=1");
  expect(scriptRuns(f)).toBe(2);
  now += 31_000;
  await get(f);
  expect(scriptRuns(f)).toBe(3);
});

test("check_projects exiting 1 with valid JSON still yields rows", async () => {
  const { body } = await get(fixture({ checkCode: 1 }));
  expect(body.errors).toEqual([]);
  expect(body.rows.some((r) => r.key === "projects/alpha")).toBe(true);
});

test("unparseable script output gives errors and a red row, not a 500", async () => {
  const { status, body } = await get(fixture({ check: "Traceback ..." }));
  expect(status).toBe(200);
  expect(body.errors[0]).toContain("check_projects.py --json failed");
  const row = body.rows.find((r) => r.key.startsWith("projects/error"));
  expect(row?.level).toBe("red");
  expect(row?.next).toContain("just projects");
});

test("factoryHub reads the path after --factory", () => {
  expect(factoryHub(["--factory", "/h"])).toBe("/h");
  expect(factoryHub([])).toBeNull();
  expect(() => factoryHub(["--factory"])).toThrow("--factory needs the hub's path");
});
