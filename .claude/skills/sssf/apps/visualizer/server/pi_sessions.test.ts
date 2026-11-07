import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { appendFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PiSessions, parseLine } from "./pi_sessions.ts";

let dir: string;
let folder: string;

const header = (cwd: string) => `${JSON.stringify({ type: "session", version: 3, id: "s1", timestamp: "2026-11-06T10:00:00Z", cwd })}\n`;
const reply = (id: string, at: string, total: number, extra: Record<string, unknown> = {}) => `${JSON.stringify({
  type: "message", id, parentId: "p", timestamp: at,
  message: { role: "assistant", api: "openai-responses", provider: "openai", model: "gpt-6.1-sol", responseId: `resp_${id}`,
    usage: { input: 100, output: 20, cacheRead: 300, cacheWrite: 0, reasoning: 5, totalTokens: 420,
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total } }, ...extra },
})}\n`;
const user = `${JSON.stringify({ type: "message", id: "u", parentId: "p", timestamp: "2026-11-06T10:01:00Z", message: { role: "user" } })}\n`;

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "pi-sessions-"));
  folder = join(dir, "--home-me-demo--");
  mkdirSync(folder);
});
afterEach(() => rmSync(dir, { recursive: true, force: true }));

const SINCE = Date.parse("2026-11-01T00:00:00Z");

describe("pi's own sessions", () => {
  test("reads each reply's cost, model and project, and skips other lines", () => {
    writeFileSync(join(folder, "a.jsonl"), header("/home/me/demo") + user + reply("1", "2026-11-06T10:02:00Z", 0.25));
    const calls = new PiSessions(dir).calls(SINCE, (cwd) => (cwd === "/home/me/demo" ? "demo" : "?"));
    expect(calls).toEqual([{ at: Date.parse("2026-11-06T10:02:00Z"), kind: "interactive", model: "openai/gpt-6.1-sol",
      cost: 0.25, input_tokens: 400, output_tokens: 20, project: "demo" }]);
  });

  test("reads only what a file appended, and leaves a half-written line for later", () => {
    const path = join(folder, "a.jsonl");
    const sessions = new PiSessions(dir);
    writeFileSync(path, header("/x") + reply("1", "2026-11-06T10:02:00Z", 1));
    expect(sessions.calls(SINCE, () => "x")).toHaveLength(1);
    const line = reply("2", "2026-11-06T10:03:00Z", 2);
    appendFileSync(path, line.slice(0, 40));
    expect(sessions.calls(SINCE, () => "x")).toHaveLength(1);
    appendFileSync(path, line.slice(40));
    expect(sessions.calls(SINCE, () => "x").map((c) => c.cost)).toEqual([1, 2]);
  });

  test("counts a reply a forked session copied once", () => {
    writeFileSync(join(folder, "a.jsonl"), header("/x") + reply("1", "2026-11-06T10:02:00Z", 1));
    writeFileSync(join(folder, "b.jsonl"), header("/x") + reply("1", "2026-11-06T10:02:00Z", 1) + reply("2", "2026-11-06T10:05:00Z", 3));
    expect(new PiSessions(dir).calls(SINCE, () => "x").map((c) => c.cost).toSorted()).toEqual([1, 3]);
  });

  test("leaves out replies before the span", () => {
    writeFileSync(join(folder, "a.jsonl"), header("/x") + reply("1", "2026-10-20T10:02:00Z", 1) + reply("2", "2026-11-06T10:02:00Z", 2));
    expect(new PiSessions(dir).calls(SINCE, () => "x").map((c) => c.cost)).toEqual([2]);
  });

  test("a missing folder or a bad line reads as nothing", () => {
    expect(new PiSessions(join(dir, "none")).calls(SINCE, () => "x")).toEqual([]);
    expect(parseLine("{not json")).toEqual({});
    expect(parseLine(user)).toEqual({});
  });
});
