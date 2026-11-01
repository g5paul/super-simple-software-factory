import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DAY_MS } from "./day.ts";
import { limitsPath, machineDay, readMachineCap } from "./machine.ts";

const NOW = Date.parse("2026-11-01T12:00:00Z");
const ago = (hours: number) => new Date(NOW - hours * 3600 * 1000).toISOString();

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "sssf-machine-"));
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

test("every repo's calls add up, each repo's share is listed largest first", () => {
  const day = machineDay([
    { name: "sandbox", calls: [{ at: ago(3), cost: 0.4 }] },
    { name: "yt", calls: [{ at: ago(5), cost: 1.0 }, { at: ago(1), cost: 0.5 }] },
    { name: "idle", calls: [] },
  ], 5, null, NOW, "/x/limits.yaml");
  expect(day.spent).toBeCloseTo(1.9);
  expect(day.repos.map((r) => r.name)).toEqual(["yt", "sandbox", "idle"]);
  expect(day.clears_at).toBeNull();
});

test("over the cap, it clears when enough of the oldest spend across repos ages out", () => {
  const day = machineDay([
    { name: "a", calls: [{ at: ago(2), cost: 0.6 }] },
    { name: "b", calls: [{ at: ago(20), cost: 0.6 }] },
  ], 1.0, null, NOW);
  expect(day.clears_at).toBe(new Date(Date.parse(ago(20)) + DAY_MS).toISOString());
});

test("the cap is limits.yaml's max_daily_cost, beside the registry", () => {
  process.env.SSSF_REGISTRY = join(dir, "projects.json");
  try {
    expect(limitsPath()).toBe(join(dir, "limits.yaml"));
    expect(readMachineCap()).toEqual({ cap: null, error: null });
    writeFileSync(join(dir, "limits.yaml"), "max_daily_cost: 25\n");
    expect(readMachineCap()).toEqual({ cap: 25, error: null });
    writeFileSync(join(dir, "limits.yaml"), "# nothing set\n");
    expect(readMachineCap()).toEqual({ cap: null, error: null });
  } finally {
    delete process.env.SSSF_REGISTRY;
  }
});

test("a broken limits.yaml says why, since the Python check stops runs on it", () => {
  const path = join(dir, "limits.yaml");
  writeFileSync(path, "max_daily_cost: lots\n");
  expect(readMachineCap(path).error).toContain("positive number");
  writeFileSync(path, "max_daily_cost: [\n");
  expect(readMachineCap(path).error).not.toBeNull();
});
