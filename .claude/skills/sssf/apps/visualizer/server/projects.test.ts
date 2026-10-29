import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Projects } from "./projects.ts";

let dir: string;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-projects-"));
  writeFileSync(join(dir, "projects.json"), JSON.stringify({ projects: { yt: { root: join(dir, "yt-summarizer") } } }));
  process.env.SSSF_REGISTRY = join(dir, "projects.json");
  mkdirSync(join(dir, "yt-summarizer", "src"), { recursive: true });
  mkdirSync(join(dir, "hub", ".git"), { recursive: true });
  mkdirSync(join(dir, "hub", ".claude", "skills", "sssf"), { recursive: true });
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  delete process.env.SSSF_REGISTRY;
});

test("a registered repo goes by its registry name, from any folder in it", () => {
  expect(new Projects(null).projectOf(join(dir, "yt-summarizer", "src"))).toBe("yt");
});

test("an unregistered folder goes by the git repo that holds it", () => {
  expect(new Projects(null).projectOf(join(dir, "hub", ".claude", "skills", "sssf"))).toBe("hub");
});

test("a folder outside any repo goes by its own name", () => {
  expect(new Projects(null).projectOf(join(dir, "loose"))).toBe("loose");
});
