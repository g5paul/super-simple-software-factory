import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { parseCheckProjects, projectRow, readSandbox, type CheckProjectsRepo } from "./factory_projects.ts";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-fprojects-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

type Over = Partial<Record<string, { ok: boolean; note?: string }>>;
function repo(over: Over = {}, extra: Partial<CheckProjectsRepo> = {}): CheckProjectsRepo {
  const checks: Over = { stamp: { ok: true }, commit: { ok: true }, clean: { ok: true }, skill: { ok: true }, quality: { ok: true, note: "a1 success" }, ...over };
  return {
    repo: "yt", branch: "main", root: "/r/yt", stamped: "abc", behind: false, at_stamp: true, ok: true,
    checks: Object.entries(checks).map(([name, c]) => ({ name, ok: c!.ok, note: c!.note ?? "" })),
    ...extra,
  };
}
const row = (r: CheckProjectsRepo, sandbox = "none", image: Parameters<typeof projectRow>[1]["image"] = null) =>
  projectRow(r, { hubSkill: "def", sandbox, image });

test("a fully passing repo is ok, and says which sandbox it defaults to", () => {
  writeFileSync(join(dir, "x"), "");
  mkdirSync(join(dir, "adws", "adw_sssf_config"), { recursive: true });
  writeFileSync(join(dir, "adws", "adw_sssf_config", "repo.yaml"), "sandbox: agentsh\nmax_daily_cost: 5\n");
  expect(readSandbox(dir)).toBe("agentsh");
  const r = row(repo(), readSandbox(dir));
  expect(r.level).toBe("ok");
  expect(r.problems).toEqual([]);
  expect(r.notes).toContain("sandbox: agentsh");
  expect(readSandbox(join(dir, "missing"))).toBe("none");
});

test("red: a dirty tree, a stamp mismatch, a failed quality run, not a git repo", () => {
  const dirty = row(repo({ clean: { ok: false, note: "2 files" } }));
  expect(dirty.level).toBe("red");
  expect(dirty.problems[0]).toBe("dirty tree on main: 2 files");
  expect(dirty.next).toContain("commit or stash");
  expect(row(repo({}, { at_stamp: false })).level).toBe("red");
  const q = row(repo({ quality: { ok: false, note: "abc123 fail" } }));
  expect(q.level).toBe("red");
  expect(q.adw_id).toBe("abc123");
  expect(row(repo({ repo: { ok: false, note: "no .git" } })).level).toBe("red");
});

test("amber: behind, no quality run since the update, a commit that misses the skill, an unbuilt image", () => {
  const behind = row(repo({}, { behind: true, at_stamp: null }));
  expect(behind.level).toBe("amber");
  expect(behind.notes).toContain("the hub doesn't know its stamp commit");
  expect(behind.next).toContain("update to skill def");
  const nq = row(repo({ quality: { ok: false, note: "no adw_quality run since abc" } }));
  expect(nq.level).toBe("amber");
  expect(nq.next).toContain("just quality");
  expect(row(repo({ commit: { ok: false, note: "x" } })).level).toBe("amber");
  const img = row(repo(), "openshell", { built: false });
  expect(img.level).toBe("amber");
  expect(img.notes).toContain("image not built");
  expect(row(repo(), "openshell", { built: true }).notes).toContain("image built");
  const unchecked = row(repo(), "openshell", { error: "podman missing" });
  expect(unchecked.level).toBe("ok");
  expect(unchecked.notes).toContain("image: not checked (podman missing)");
});

test("a quality run still running is not red", () => {
  const r = row(repo({ quality: { ok: false, note: "abc123 running" } }));
  expect(r.level).toBe("ok");
});

test("parseCheckProjects wants JSON with a repos array", () => {
  expect(parseCheckProjects('{"skill":"x","repos":[]}').repos).toEqual([]);
  expect(() => parseCheckProjects("Traceback")).toThrow();
  expect(() => parseCheckProjects("{}")).toThrow();
});
