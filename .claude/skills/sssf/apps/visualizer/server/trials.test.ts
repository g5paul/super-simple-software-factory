import { describe, expect, test } from "bun:test";
import { leaderboard, runTrial, trialView, type AttemptRow } from "./trials.ts";
import type { TrialAttemptCard, TrialView } from "../shared/types.ts";

function row(adw_id: string, roster: string, status: AttemptRow["status"], cost: number): AttemptRow {
  return {
    adw_id, status, cost, tokens: 100, request: "make it two",
    payload_json: JSON.stringify({ trial: "t1", roster, roster_file: `adws/adw_sssf_config/${roster}.yaml`, roster_sha256: `sha-${roster}`, family: roster === "main" ? "claude" : "openai", base: "abc1234" }),
    started_at: "2026-11-01T10:00:00.000+00:00", ended_at: status === "running" ? null : "2026-11-01T10:05:00.000+00:00",
  };
}

function attempt(adw_id: string, roster: string, cost: number, passed: boolean, seconds = 300): TrialAttemptCard {
  return {
    adw_id, roster, roster_file: `f/${roster}`, roster_sha256: `sha-${roster}`, family: "claude",
    status: "success", cost, tokens: 1, seconds, checks: true, live: false,
    review: passed ? "pass" : "block", rulings: [], rank: 1, rank_others: null, passed,
  };
}

function card(trial: string, attempts: TrialAttemptCard[]): Omit<TrialView, "scored"> {
  return {
    trial, request: "r", base: "b", families: ["claude"], panel: "p", panel_status: "running",
    panel_cost: 0.1, total_cost: 1, suggested: null, why: "", merged: null, running: false, attempts,
  };
}

describe("trialView", () => {
  test("before a panel, the attempts show as their runs stand", () => {
    const view = trialView("t1", [row("a", "main", "running", 0.2), row("b", "chatgpt", "success", 0.3)], null, null);
    expect(view.scored).toBe(false);
    expect(view.running).toBe(true);
    expect(view.attempts.map((a) => [a.adw_id, a.roster, a.live])).toEqual([["a", "main", true], ["b", "chatgpt", false]]);
    expect(view.attempts[1]!.seconds).toBe(300);
    expect(view.total_cost).toBeCloseTo(0.5);
    expect(view.families).toEqual(["claude", "openai"]);
    expect(view.why).toContain("still running");
  });

  test("the panel's scorecard is shown with the runs' current status and the merge", () => {
    const view = trialView("t1", [row("a", "main", "success", 0.2)], card("t1", [attempt("a", "main", 0.2, true)]), "a");
    expect(view.scored).toBe(true);
    expect(view.merged).toBe("a");
    expect(view.attempts[0]!.status).toBe("success");
  });
});

describe("leaderboard", () => {
  test("one row per roster and hash, with wins and medians over passing attempts", () => {
    const cards = [
      card("t1", [attempt("a1", "main", 1.0, true, 100), attempt("b1", "chatgpt", 0.2, true, 300)]),
      card("t2", [attempt("a2", "main", 3.0, true, 200), attempt("b2", "chatgpt", 0.4, false)]),
    ];
    const rows = leaderboard(cards, new Set(["a2", "b1"]), (file) => (file === "f/main" ? "sha-main" : "other"));
    const main = rows.find((r) => r.roster === "main")!;
    expect(main).toMatchObject({ trials: 2, attempts: 2, passed: 2, wins: 1, median_cost: 2, median_seconds: 150, current: true });
    const gpt = rows.find((r) => r.roster === "chatgpt")!;
    expect(gpt).toMatchObject({ trials: 2, passed: 1, wins: 1, median_cost: 0.2, current: false });
  });

  test("an edited roster gets its own row", () => {
    const edited = attempt("a3", "main", 1, true);
    edited.roster_sha256 = "sha-new";
    const rows = leaderboard([card("t1", [attempt("a1", "main", 1, true)]), card("t3", [edited])], new Set(), () => "sha-new");
    expect(rows.map((r) => [r.sha256, r.current])).toEqual([["sha-main", false], ["sha-new", true]]);
  });
});

describe("runTrial", () => {
  test("an attempt names its trial and roster; a panel names its trial", () => {
    const ev = (name: string, p: object) => ({ type: "log", name, phase_id: "", payload_json: JSON.stringify(p) });
    expect(runTrial([ev("trial", { trial: "t1", roster: "chatgpt" })])).toEqual({ trial: "t1", role: "attempt", roster: "chatgpt" });
    expect(runTrial([ev("trial_panel", { trial: "t1" })])).toEqual({ trial: "t1", role: "panel", roster: null });
    expect(runTrial([ev("run_caps", {})])).toBeNull();
  });

  test("an attempt carries what its roster runs on", () => {
    const ev = { type: "log", name: "trial", phase_id: "", payload_json: JSON.stringify({ trial: "t1", roster: "main", coding_agent: "Claude Code" }) };
    expect(runTrial([ev])?.coding_agent).toBe("Claude Code");
  });
});

describe("roster labels", () => {
  test("a leaderboard row takes its coding agent from an attempt that recorded one", () => {
    const old = attempt("a1", "main", 1, true);
    const recent = { ...attempt("a2", "main", 1, true), coding_agent: "Claude Code" };
    const [only] = leaderboard([card("t1", [old]), card("t2", [recent])], new Set(), () => "sha-main");
    expect(only?.coding_agent).toBe("Claude Code");
  });

  test("an unscored trial's attempts carry it from their tags", () => {
    const tagged = row("a", "main", "running", 0.2);
    tagged.payload_json = JSON.stringify({ ...JSON.parse(tagged.payload_json), coding_agent: "Claude Code" });
    expect(trialView("t1", [tagged], null, null).attempts[0]?.coding_agent).toBe("Claude Code");
  });
});
