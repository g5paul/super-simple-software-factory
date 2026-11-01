import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DAY_MS, daySpend, readDailyCap } from "./day.ts";

const NOW = Date.parse("2026-10-31T12:00:00Z");
const ago = (hours: number) => new Date(NOW - hours * 3600 * 1000).toISOString();

let root: string;
beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), "sssf-day-"));
  mkdirSync(join(root, "adws", "adw_sssf_config"), { recursive: true });
});
afterEach(() => rmSync(root, { recursive: true, force: true }));

const repoYaml = (text: string) => writeFileSync(join(root, "adws", "adw_sssf_config", "repo.yaml"), text);

test("under the cap, the spend shows and nothing clears", () => {
  const day = daySpend([{ at: ago(3), cost: 0.4 }, { at: ago(1), cost: 0.3 }], 1.0, NOW);
  expect(day.spent).toBeCloseTo(0.7);
  expect(day.cap).toBe(1.0);
  expect(day.clears_at).toBeNull();
  expect(day.since).toBe(new Date(NOW - DAY_MS).toISOString());
});

test("over the cap, it clears when enough of the oldest spend ages out", () => {
  const day = daySpend([{ at: ago(20), cost: 0.6 }, { at: ago(2), cost: 0.6 }], 1.0, NOW);
  expect(day.clears_at).toBe(new Date(Date.parse(ago(20)) + DAY_MS).toISOString());
});

test("at the cap exactly is over it, as the check has it", () => {
  expect(daySpend([{ at: ago(5), cost: 1.0 }], 1.0, NOW).clears_at).toBe(new Date(Date.parse(ago(5)) + DAY_MS).toISOString());
});

test("with no cap, the spend still shows", () => {
  const day = daySpend([{ at: ago(1), cost: 40 }], null, NOW);
  expect(day).toMatchObject({ spent: 40, cap: null, clears_at: null });
});

test("the cap is repo.yaml's max_daily_cost", () => {
  repoYaml("merge: any\nmax_worktree_runs: 2\n# max_live_cost: 7.50\nmax_daily_cost: 20.00\n");
  expect(readDailyCap(root)).toBe(20);
});

test("a missing, commented-out, or broken repo.yaml reads as no cap", () => {
  expect(readDailyCap(root)).toBeNull();
  repoYaml("#   max_daily_cost: 20.00\n");
  expect(readDailyCap(root)).toBeNull();
  repoYaml("max_daily_cost: [unclosed\n");
  expect(readDailyCap(root)).toBeNull();
  repoYaml("max_daily_cost: lots\n");
  expect(readDailyCap(root)).toBeNull();
});
