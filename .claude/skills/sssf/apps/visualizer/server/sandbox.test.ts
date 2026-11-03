import { expect, test } from "bun:test";
import { sandboxUsage, type RepoEnds } from "./sandbox.ts";

const NOW = Date.parse("2026-11-03T00:00:00Z");
const daysAgo = (d: number) => new Date(NOW - d * 86_400_000).toISOString().replace("Z", "+00:00");

function repo(name: string, ends: [number, object][]): RepoEnds {
  return { name, ends: ends.map(([d, p]) => ({ at: daysAgo(d), payload_json: JSON.stringify(p) })) };
}

test("Vercel counts the last 30 days and E2B all time, over every repo", () => {
  const usage = sandboxUsage([
    repo("a", [
      [40, { provider: "vercel", vm: "v-old", vm_seconds: 3600, cpu_seconds: 900, memory_gb_hours: 4 }],
      [2, { provider: "vercel", vm: "v-new", vm_seconds: 1800, cpu_seconds: 360, memory_gb_hours: 2 }],
      [40, { provider: "e2b", vm: "e-old", vm_seconds: 1000, cpu_seconds: 50, vm_cost_usd: 0.046 }],
    ]),
    repo("b", [[1, { provider: "e2b", vm: "e-new", vm_seconds: 1000, cpu_seconds: null, vm_cost_usd: 0.046 }]]),
  ], NOW);
  const [vercel, e2b] = usage.providers;
  expect(vercel).toMatchObject({ provider: "vercel", window_days: 30, runs: 1, cpu_seconds: 360, gb_hours: 2 });
  expect(vercel.allowed).toEqual({ cpu_hours: 5, gb_hours: 420, creations: 5000 });
  expect(e2b).toMatchObject({ provider: "e2b", window_days: null, runs: 2, cpu_unknown: 1 });
  expect(e2b.cost_usd).toBeCloseTo(0.092);
  expect(e2b.repos).toEqual([{ name: "a", runs: 1 }, { name: "b", runs: 1 }]);
});

test("older runs are estimated from VM seconds, and a VM read twice counts once", () => {
  const old = repo("a", [
    [1, { provider: "vercel", vm: "v1", vm_seconds: 3600 }],
    [1, { provider: "e2b", vm: "e1", vm_seconds: 1000 }],
  ]);
  const [vercel, e2b] = sandboxUsage([old, old], NOW).providers;
  expect(vercel.runs).toBe(1);
  expect(vercel.gb_hours).toBeCloseTo(4);
  expect(vercel.cpu_unknown).toBe(1);
  expect(e2b.cost_usd).toBeCloseTo(1000 * (2 * 0.000014 + 4 * 0.0000045));
});

test("no VM runs means no providers, so the gauge stays hidden", () => {
  expect(sandboxUsage([repo("a", [])], NOW).providers).toEqual([]);
  expect(sandboxUsage([{ name: "a", ends: [{ at: daysAgo(1), payload_json: "not json" }] }], NOW).providers).toEqual([]);
});
