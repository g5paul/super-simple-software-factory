import { expect, test } from "bun:test";
import type { Phase } from "../shared/types.ts";
import { caps, leftovers, sandbox, stopReason, ticketRun, worktree, type RunEvent } from "./runview.ts";

let seq = 0;
function phase(name: string, status: Phase["status"], error: string | null = null): Phase {
  seq += 1;
  return {
    phase_id: `a1_${String(seq).padStart(2, "0")}_${name}`, adw_id: "a1", seq, name, kind: "code",
    owner: "x", description: null, status, attempt: 0, retries: 0, error, started_at: null, ended_at: null,
  };
}

function ev(type: string, name: string, payload: object, phase_id = ""): RunEvent {
  return { type, name, phase_id, payload_json: JSON.stringify(payload) };
}

const listing = ev("log", "tickets", {
  feature: "summaries",
  folder: ".scratch/summaries",
  tickets: [
    { number: "01", title: "A", path: "p1", done: true },
    { number: "03", title: "C", path: "p3", done: false },
    { number: "02", title: "B", path: "p2", done: false },
    { number: "04", title: "D", path: "p4", done: false },
  ],
});

test("a ticket run is grouped by ticket, in build order", () => {
  const phases = [
    phase("read", "success"),
    phase("t03_build", "success"),
    phase("t03_verify_1", "success"),
    phase("t03_fix_1", "success"),
    phase("t03_verify_2", "success"),
    phase("t03_commit", "success"),
    phase("t02_build", "success"),
    phase("t02_verify_1", "fail", "boom"),
  ];
  const ends = [
    ev("agent_end", "ticket_builder", { cost: 0.25 }, phases[1].phase_id),
    ev("agent_end", "ticket_builder", { cost: 0.05 }, phases[3].phase_id),
    ev("agent_end", "ticket_builder", { cost: 0.4 }, phases[6].phase_id),
    ev("agent_end", "scout", { cost: 0.01 }, "a1_99_scout"),
  ];
  const run = ticketRun([listing, ...ends], phases);
  expect(run?.feature).toBe("summaries");
  expect(run?.tickets.map((t) => [t.number, t.status, t.retries, Number(t.cost.toFixed(2))])).toEqual([
    ["01", "done_before", 0, 0],
    ["03", "done", 1, 0.3],
    ["02", "fail", 0, 0.4],
    ["04", "queued", 0, 0],
  ]);
  expect(run?.tickets[1].phase_id).toBe(phases[1].phase_id);
});

test("a run that is not a ticket run has no ticket table", () => {
  expect(ticketRun([], [phase("build", "success")])).toBeNull();
});

test("a cap stop is the reason, over the phase it failed", () => {
  const phases = [phase("build", "fail", "RunBudgetStop: run spent $5.10")];
  const events = [ev("error", "budget_stop", { error: "run a1 spent $5.10 of max_run_cost $5.00" }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")).toEqual({
    kind: "budget_stop", message: "run a1 spent $5.10 of max_run_cost $5.00", phase_id: phases[0].phase_id,
  });
});

test("the live runs' shared cap is a stop reason of its own", () => {
  const phases = [phase("build", "fail", "LiveBudgetStop: the live runs have spent $1.10")];
  const events = [ev("error", "live_budget_stop", { error: "the live runs have spent $1.10 together" }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")?.kind).toBe("live_budget_stop");
});

test("a forbidden command is a stop reason of its own", () => {
  const phases = [phase("build", "fail", "RuleBreach: builder ran `git push`")];
  const events = [ev("error", "rule_breach", { error: "builder ran `git push`, which this repo's rule forbids" }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")).toMatchObject({ kind: "rule_breach", message: "builder ran `git push`, which this repo's rule forbids" });
});

test("an agent that went quiet is a stop reason of its own", () => {
  const phases = [phase("build", "fail", "SilentStop: builder wrote nothing for 15 minutes")];
  const events = [ev("error", "agent_silent", { error: "builder wrote nothing for 15 minutes and was stopped", minutes: 15 }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")).toMatchObject({ kind: "agent_silent", message: "builder wrote nothing for 15 minutes and was stopped" });
});

test("the repo's daily cap is a stop reason of its own", () => {
  const phases = [phase("build", "fail", "DailyBudgetStop: this repo's runs have spent $20.10")];
  const events = [ev("error", "daily_budget_stop", { error: "this repo's runs have spent $20.10 in the last 24 hours" }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")?.kind).toBe("daily_budget_stop");
});

test("a run that finished its phases but was not accepted says why", () => {
  const phases = [phase("t02_verify_3", "success")];
  const events = [ev("error", "not_accepted", { reason: "ticket 02 still failed" }, phases[0].phase_id)];
  expect(stopReason(events, phases, "fail")?.message).toBe("ticket 02 still failed");
});

test("otherwise the first failed phase is the reason", () => {
  const phases = [phase("plan", "success"), phase("build", "fail", "builder failed gates")];
  expect(stopReason([], phases, "fail")).toEqual({
    kind: "phase_failed", message: "build: builder failed gates", phase_id: phases[1].phase_id,
  });
});

test("a successful or running run has no stop reason", () => {
  const phases = [phase("build", "fail", "x")];
  expect(stopReason([], phases, "success")).toBeNull();
  expect(stopReason([], phases, "running")).toBeNull();
});

test("the latest recorded caps win, and an old run has none", () => {
  expect(caps([])).toBeNull();
  expect(caps([
    ev("log", "run_caps", { max_run_cost: 5, rate_limit_stop: 0.8 }),
    ev("log", "run_caps", { max_run_cost: 8, rate_limit_stop: null }),
  ])).toEqual({ max_run_cost: 8, rate_limit_stop: null });
});

test("a --worktree run reports its branch, and a plain run has none", () => {
  const place = { branch: "sssf/a1", worktree: "/r/adws/adw_data/worktrees/a1", launch_branch: "main",
                  base: "abc1234", launch_dirty: true };
  expect(worktree([ev("log", "worktree", place)])).toEqual({ ...place, landed: null, rebased: null });
  expect(worktree([ev("log", "run_caps", { max_run_cost: 5 })])).toBeNull();
  expect(worktree([{ type: "log", name: "worktree", phase_id: "", payload_json: "{oops" }])).toBeNull();
});

test("a merged or discarded branch says so, and the latest end wins", () => {
  const start = ev("log", "worktree", { branch: "sssf/a1", worktree: "/w", launch_branch: "main",
                                         base: "abc1234", launch_dirty: false });
  const merged = ev("log", "worktree_merged", { into: "main", how: "fast-forward", head: "def5678" });
  expect(worktree([start, merged])?.landed).toEqual({ state: "merged", into: "main", how: "fast-forward",
                                                      head: "def5678" });
  expect(worktree([start, ev("log", "worktree_discarded", {})])?.landed).toEqual({ state: "discarded" });
});

test("a rebase is reported, the latest attempt wins, and a run never rebased has none", () => {
  const start = ev("log", "worktree", { branch: "sssf/a1", worktree: "/w", launch_branch: "main",
                                         base: "abc1234", launch_dirty: false });
  const conflict = ev("log", "worktree_rebased", { onto: "main", old: "abc1234", conflict: true, kept: false });
  const kept = ev("log", "worktree_rebased", { onto: "main", old: "abc1234", new: "fed4321", checks: "c1",
                                               passed: true, kept: true });
  expect(worktree([start])?.rebased).toBeNull();
  expect(worktree([start, conflict])?.rebased).toEqual({ onto: "main", old: "abc1234", conflict: true, kept: false });
  expect(worktree([start, conflict, kept])?.rebased?.kept).toBe(true);
});

test("a ChatGPT plan's usage stop carries its provider; Claude's does not", () => {
  const phases = [phase("build", "fail", "UsageLimitStop: the ChatGPT plan hit its usage limit")];
  const pi = [ev("error", "rate_limit_stop", { provider: "openai", error: "The ChatGPT plan behind pi's sign-in hit its usage limit" }, phases[0].phase_id)];
  expect(stopReason(pi, phases, "fail")).toMatchObject({ kind: "rate_limit_stop", provider: "openai" });
  const claude = [ev("error", "rate_limit_stop", { error: "Claude's 5-hour window is at 85%" }, phases[0].phase_id)];
  expect(stopReason(claude, phases, "fail")?.provider).toBeUndefined();
});

test("a sandbox run shows its VM, what came back, and how the VM ended", () => {
  const vm = "irq4wy";
  const events = [
    ev("log", "sandbox", { kind: "code", owner: "sandbox" }),
    ev("log", "sandbox", { message: "▶ 01 sandbox" }),
    ev("log", "sandbox", { provider: "e2b", vm, session_minutes: 60, python: "3.13" }),
    ev("log", "sandbox", { provider: "e2b", vm, swept: [] }),
    ev("log", "sandbox_commits", { vm, count: 1, head: "abc" }),
    ev("log", "sandbox_refresh", { vm, refreshes: 1 }),
    ev("log", "sandbox_commits", { vm, count: 2, head: "def" }),
    ev("log", "sandbox_end", { provider: "e2b", vm, vm_seconds: 20, cpu_seconds: 4.6, deleted: true,
                               uncommitted_patch: "", vcpus: 2, memory_gib: 4, vm_cost_usd: 0.0009 }),
  ];
  const box = sandbox(events);
  expect(box).toMatchObject({ provider: "e2b", vm, python: "3.13", session_minutes: 60, commits: 3, refreshes: 1, error: null });
  expect(box?.end?.cpu_seconds).toBe(4.6);
  expect(box?.end?.vm_cost_usd).toBe(0.0009);
});

test("a sandbox run whose VM is still up has no end, and a failed delete says why", () => {
  const vm = "plum";
  const up = [ev("log", "sandbox", { provider: "vercel", vm, session_minutes: 45, python: "" })];
  expect(sandbox(up)?.end).toBeNull();
  expect(sandbox(up)?.python).toBeUndefined();
  const ended = [...up,
    ev("log", "sandbox_error", { vm, error: "delete failed: 500" }),
    ev("log", "sandbox_end", { provider: "vercel", vm, vm_seconds: 9, deleted: false, uncommitted_patch: "" })];
  expect(sandbox(ended)?.error).toBe("delete failed: 500");
  expect(sandbox(ended)?.end?.deleted).toBe(false);
});

test("a run on the host has no sandbox, even with a phase named sandbox", () => {
  expect(sandbox([ev("log", "sandbox", { message: "▶ 01 sandbox" })])).toBeNull();
  expect(sandbox([])).toBeNull();
});


test("a VM's tool check shows on its sandbox, with any notes", () => {
  const events = [
    ev("log", "sandbox", { provider: "vercel", vm: "v1", python: "3.13", session_minutes: 45 }),
    ev("log", "sandbox_tools", { vm: "v1", tools: { git: "git version 2.43.0", pi: "2.0.1" },
                          notes: ["pi 2.0.1 is a newer major than SSSF has run on (1.x); the run goes on"], problems: [] }),
  ];
  const box = sandbox(events);
  expect(box?.tools).toEqual({ git: "git version 2.43.0", pi: "2.0.1" });
  expect(box?.tool_notes).toHaveLength(1);
  expect(sandbox(events.slice(0, 1))?.tools).toBeUndefined();
});

test("an agent whose leftovers were stopped is listed, in order", () => {
  const events = [
    ev("log", "leftovers_stopped", { agent: "scout", minutes: 15 }, "a1_02_scout"),
    ev("log", "rule_refused", { agent: "builder" }, "a1_03_build"),
    ev("log", "leftovers_stopped", { agent: "builder", minutes: 15 }, "a1_03_build"),
  ];
  expect(leftovers(events)).toEqual([
    { agent: "scout", phase_id: "a1_02_scout" },
    { agent: "builder", phase_id: "a1_03_build" },
  ]);
  expect(leftovers([])).toEqual([]);
});
