import { expect, test } from "bun:test";
import type { Phase } from "../shared/types.ts";
import { caps, stopReason, ticketRun, worktree, type RunEvent } from "./runview.ts";

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
  expect(worktree([ev("log", "worktree", place)])).toEqual({ ...place, landed: null });
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
