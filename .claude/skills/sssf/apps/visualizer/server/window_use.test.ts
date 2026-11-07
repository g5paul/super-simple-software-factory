import { describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { closedWeek, readArchive, readHistory, windowUse, type Reading } from "./window_use.ts";

const H = 3_600_000;
const RESET = Date.parse("2026-11-13T05:00:00Z");
const START = RESET - 7 * 24 * H;
const live = (u: number) => ({ utilization: u, resets_at: new Date(RESET).toISOString() });
const line = (at: number, u: number, reset = RESET): Reading => ({ at, w: { seven_day: [u, reset / 1000] } });

describe("a window's use in percent per hour", () => {
  test("since it started counts from a window's empty start", () => {
    const got = windowUse([], "seven_day", live(0.1), START + 20 * H);
    expect(got.since_start?.pct_per_hour).toBeCloseTo(0.5);
    expect(got.since_start?.hours).toBeCloseTo(20);
  });

  test("the recent rate measures the last 24 hours from the reading in effect then", () => {
    const at = START + 48 * H;
    const history = [line(START + 10 * H, 0.04), line(START + 23.5 * H, 0.06), line(START + 40 * H, 0.1)];
    const got = windowUse(history, "seven_day", live(0.12), at);
    // 6% held at the span's start, 24 hours back; 12% now.
    expect(got.recent?.points).toBeCloseTo(6);
    expect(got.recent?.hours).toBeCloseTo(24);
    expect(got.recent?.pct_per_hour).toBeCloseTo(0.25);
  });

  test("after a gap in the readings it measures from the last one before it", () => {
    const at = START + 48 * H;
    const got = windowUse([line(START + 10 * H, 0.04)], "seven_day", live(0.1), at);
    expect(got.recent?.from).toBe(new Date(START + 10 * H).toISOString());
    expect(got.recent?.hours).toBeCloseTo(38);
    expect(got.recent?.points).toBeCloseTo(6);
  });

  test("leaves out the last window's readings and other windows' resets", () => {
    const at = START + 30 * H;
    const history = [line(START - 2 * H, 0.9, RESET - 7 * 24 * H), line(START + 5 * H, 0.5, RESET + 24 * H)];
    const got = windowUse(history, "seven_day", live(0.03), at);
    expect(got.recent?.from).toBe(new Date(START).toISOString());
    expect(got.recent?.points).toBeCloseTo(3);
  });

  test("a window under 15 minutes old has no rate yet", () => {
    const got = windowUse([], "five_hour", { utilization: 0.01, resets_at: new Date(RESET).toISOString() }, RESET - 5 * H + 5 * 60_000);
    expect(got).toEqual({ since_start: null, recent: null });
  });

  test("reads the history file, skipping a broken line", () => {
    const dir = mkdtempSync(join(tmpdir(), "window-use-"));
    try {
      const path = join(dir, "h.jsonl");
      writeFileSync(path, `{"at":"2026-11-07T02:00:00Z","w":{"seven_day":[0.03,${RESET / 1000}]}}\n{"at":`);
      expect(readHistory(path)).toEqual([{ at: Date.parse("2026-11-07T02:00:00Z"), w: { seven_day: [0.03, RESET / 1000] } }]);
      expect(readHistory(join(dir, "none"))).toEqual([]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe("a closed week", () => {
  const read = (at: number, u: number, reset: number): Reading => ({ at, w: { seven_day: [u, reset / 1000] } });

  test("is the last window's peak, once a reading names a new reset after it ended", () => {
    const history = [read(START + H, 0.1, RESET), read(RESET - H, 0.4, RESET)];
    const got = closedWeek(history, read(RESET + H, 0.01, RESET + 7 * 24 * H), "chatgpt");
    expect(got).toEqual({ provider: "chatgpt", kind: "seven_day", start: START / 1000, end: RESET / 1000, peak: 0.4,
      last_at: new Date(RESET - H).toISOString() });
  });

  test("isn't a window whose reset moved before it ended, or the same window", () => {
    const history = [read(START + H, 0.0, RESET)];
    expect(closedWeek(history, read(START + 2 * H, 0.0, RESET + H), "chatgpt")).toBeNull();
    expect(closedWeek(history, read(START + 2 * H, 0.1, RESET), "chatgpt")).toBeNull();
    expect(closedWeek([], read(START, 0.1, RESET), "chatgpt")).toBeNull();
  });

  test("reads the archive newest first, skipping a broken line", () => {
    const dir = mkdtempSync(join(tmpdir(), "archive-"));
    try {
      const path = join(dir, "a.jsonl");
      writeFileSync(path, `${JSON.stringify({ provider: "claude", kind: "seven_day", start: 1, end: 2, peak: 0.3, last_at: "x" })}\n`
        + `${JSON.stringify({ provider: "chatgpt", kind: "seven_day", start: 3, end: 4, peak: 0.1, last_at: "y" })}\n{"prov`);
      expect(readArchive(path).map((a) => a.end)).toEqual([4, 2]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
