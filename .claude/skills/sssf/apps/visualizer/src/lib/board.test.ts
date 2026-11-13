import { describe, expect, test } from "bun:test";
import { DEFAULT_LEVELS, levelCounts, parseLevels, visibleRows } from "./board.ts";

const row = (key: string, section: "projects" | "machine" | "runs", level: "red" | "amber" | "ok") => ({ key, section, level });
const rows = [
  row("r1", "runs", "red"), row("r2", "runs", "amber"), row("r3", "runs", "ok"),
  row("p1", "projects", "amber"), row("m1", "machine", "red"),
];

describe("the board's level toggles", () => {
  test("open red and amber, and close ok, by default", () => {
    expect([...DEFAULT_LEVELS].toSorted()).toEqual(["amber", "red"]);
    expect(visibleRows(rows, null, DEFAULT_LEVELS).map((r) => r.key)).toEqual(["r1", "r2", "p1", "m1"]);
  });

  test("a closed level hides its rows, and the section tile still filters", () => {
    expect(visibleRows(rows, "runs", new Set(["red"])).map((r) => r.key)).toEqual(["r1"]);
    expect(visibleRows(rows, null, new Set()).map((r) => r.key)).toEqual([]);
  });

  test("counts each level in view, open or closed, so a closed level still shows its count", () => {
    expect(levelCounts(rows, null)).toEqual({ red: 2, amber: 2, ok: 1 });
    expect(levelCounts(rows, "runs")).toEqual({ red: 1, amber: 1, ok: 1 });
  });

  test("a stored choice reads back, and anything unreadable falls back to the default", () => {
    expect([...parseLevels("red,ok")].toSorted()).toEqual(["ok", "red"]);
    expect([...parseLevels("")]).toEqual([]);
    expect(parseLevels(null)).toEqual(DEFAULT_LEVELS);
    expect(parseLevels("blue,green")).toEqual(DEFAULT_LEVELS);
  });
});
