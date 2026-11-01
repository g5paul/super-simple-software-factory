/**
 * Types shared by the read-only server and the Vue client.
 *
 * Every interface mirrors a table in sssf.db one-for-one (see
 * references/observability.md). Nothing here is derived state: phase durations,
 * session progress and lane layout are computed in the UI, never stored.
 */

/** sessions.status — a run is running until it earns success. */
export type SessionStatus = "running" | "success" | "fail";

/** phases.status — queued only for manifest-declared phases not yet entered. */
export type PhaseStatus = "queued" | "running" | "success" | "fail";

/** phases.kind — decides which lane a block renders in. */
export type PhaseKind = "engineer" | "code" | "agent";

/** events.type — the ten types tracer.py emits. */
export type EventType =
  | "phase_start"
  | "phase_end"
  | "agent_start"
  | "agent_end"
  | "tool_call"
  | "handoff"
  | "gate_pass"
  | "gate_fail"
  | "log"
  | "error";

export interface Session {
  adw_id: string;
  /** ADW script(s) that ran this session, e.g. "adw_plan + adw_build_test". */
  adw_name: string | null;
  request: string | null;
  status: SessionStatus | null;
  engineer: string | null;
  started_at: string | null;
  ended_at: string | null;
  total_tokens: number | null;
  total_cost: number | null;
  /** 1 once archived out of the review list. Review state, not run state. */
  archived: number | null;
}

/**
 * A session row with its phases embedded, so the L1 table draws the
 * mini-progress dots without a second request per row.
 */
export interface SessionSummary extends Session {
  /** Full phase rows, ordered by seq — one dot each. */
  phases: Phase[];
  phase_count: number;
  /**
   * The session's agents, same shape and merge rules as SessionDetail.agents —
   * so an L1 card can color its per-agent dots without a request per card.
   */
  agents: AgentSession[];
  /** Where a `--worktree` run worked; null for a run on the checked-out branch. */
  worktree: RunWorktree | null;
}

export interface Phase {
  phase_id: string;
  adw_id: string;
  seq: number | null;
  name: string | null;
  kind: PhaseKind | null;
  owner: string | null;
  description: string | null;
  status: PhaseStatus | null;
  attempt: number | null;
  retries: number | null;
  error: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface Event {
  /** SQLite rowid — the polling cursor. Monotonic, insertion-ordered. */
  rowid: number;
  event_id: string;
  adw_id: string;
  phase_id: string | null;
  /** Span nesting: an agent phase expands into its tool-call children. */
  parent_id: string | null;
  type: EventType | null;
  name: string | null;
  /** Raw JSON string as written by the tracer; parse at the point of display. */
  payload_json: string | null;
  tokens: number | null;
  started_at: string | null;
  ended_at: string | null;
}

export interface Envelope {
  envelope_id: string;
  adw_id: string;
  phase_id: string | null;
  agent: string | null;
  /** Name of the data_types model the response was parsed against. */
  output_type: string | null;
  payload_json: string | null;
  /** SQLite integer boolean. */
  valid: number | null;
  attempt: number | null;
  created_at: string | null;
}

export interface GateResult {
  id: number;
  adw_id: string;
  phase_id: string | null;
  attempt: number | null;
  gate: string | null;
  /** SQLite integer boolean. */
  passed: number | null;
  /** JSON array of violation strings; "[]" on a pass. */
  violations_json: string | null;
  /**
   * JSON array of GateCheck — the per-item evidence behind the verdict, so a
   * green gate can say WHAT it verified rather than only that it passed.
   * Null on rows written before the tracer recorded checks; those are not
   * backfilled, so fall back to the verdict alone.
   */
  checks_json: string | null;
  created_at: string | null;
}

/** One item a gate inspected — the parsed element of `checks_json`. */
export interface GateCheck {
  item: string;
  ok: boolean;
  note: string;
}

/** agent_sessions — the queryable mirror of agent_map.json. Supplies lane labels (`name · model`). */
export interface AgentSession {
  adw_id: string;
  agent: string;
  coding_agent: string | null;
  model: string | null;
  session_id: string | null;
  /**
   * The agent's lane color from sssf.config.yaml, e.g. "#a78bfa". Null on dbs
   * written by a tracer predating the column, and on agents with no configured
   * color — fall back to the UI's own palette.
   */
  color: string | null;
  /**
   * How full the agent's context window was after its last turn, and the
   * model's ceiling. Null on dbs predating the columns and on an agent still
   * running — the lane draws no bar rather than a misleading empty one.
   */
  context_tokens: number | null;
  context_window: number | null;
  created_at: string | null;
  last_used_at: string | null;
}

// ── payload_json shapes ──────────────────────────────────────────────────────
// events.payload_json is stored as a string. These are the parsed shapes for
// the two payloads the UI renders; every field is optional because the tracer
// writes what the coding agent reported, which varies by agent and by version.

/** Parsed `agent_start` payload — the live source of a lane's label and color. */
export interface AgentStartPayload {
  model?: string;
  thinking?: string;
  session_id?: string;
  color?: string;
  coding_agent?: string;
  purpose?: string;
  /** Tool allowlist; null means all tools. Absent on pre-config-payload rows. */
  tools?: string[] | null;
  harness_engineering?: string[];
}

/**
 * Tokens and dollars per component for one agent phase, summed across every
 * send it made (a retried phase paid more than once). Mirrors pi's `usage`:
 * `input_tokens` EXCLUDES cache reads, which bill at their own rate.
 */
export interface UsageBreakdown {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  /**
   * Thinking tokens — the reasoning SHARE of `output_tokens`, not a fifth
   * component. Billed at the output rate; adding it to the others would
   * double-count. Absent (undefined) on runs predating the field.
   */
  reasoning_tokens?: number;
  total_tokens: number;
  input_cost: number;
  output_cost: number;
  cache_read_cost: number;
  cache_write_cost: number;
  total_cost: number;
}

/** Parsed `agent_end` payload — closes out a call with its cost and context use. */
export interface AgentEndPayload {
  cost?: number;
  /** Absent on runs predating the breakdown; `cost` alone survives there. */
  usage?: UsageBreakdown;
  /** Window occupancy after the final turn, and the model's ceiling. */
  context_tokens?: number;
  context_window?: number;
}

/**
 * Parsed `tool_call` payload — one event per real tool call, emitted when the
 * tool returns. `result_snippet` and `duration_ms` are absent when the coding
 * agent never reported a result.
 */
export interface ToolCallPayload {
  tool?: string;
  tool_call_id?: string;
  args?: Record<string, unknown>;
  result_snippet?: string;
  ok?: boolean;
  duration_ms?: number;
  agent?: string;
}

// ── API responses ────────────────────────────────────────────────────────────

/** GET /api/sessions */
export type SessionsResponse = SessionSummary[];

/** GET /api/sessions/:adw_id */
/**
 * What actually moved through a session, summed across every agent.
 *
 * Deliberately NOT the billed total: `sessions.total_tokens` also counts every
 * cached re-read, which is the same context charged again on each turn.
 */
export interface SessionUsage {
  /** Raw prompt tokens read for the first time: new input + cache writes. */
  read: number;
  /** Tokens generated. Each produced exactly once, so this needs no adjusting. */
  written: number;
}

/** The caps a run recorded at its start. null means that cap is off. */
export interface RunCaps {
  max_run_cost: number | null;
  rate_limit_stop: number | null;
  /** repo.yaml's cap on what live runs spend together. Absent on runs from before it existed. */
  max_live_cost?: number | null;
  /** repo.yaml's cap on what the repo spent in the last 24 hours. Absent on runs from before it existed. */
  max_daily_cost?: number | null;
  /** limits.yaml's cap on what every repo on the machine spent in the last 24 hours. */
  machine_daily_cost?: number | null;
}

/** Where a `--worktree` run worked, from the `worktree` event its start logged. */
export interface RunWorktree {
  /** sssf/<adw_id> */
  branch: string;
  /** The run's own folder, under adws/adw_data/worktrees/. */
  worktree: string;
  /** The branch it was cut from, and `just merge` lands it on. */
  launch_branch: string;
  /** The launch branch's commit when the run started. */
  base: string;
  /** The launch checkout had uncommitted changes, which the run left out. */
  launch_dirty: boolean;
  /** How this repo runs SSSF's recipes, `just` or `just sssf`. Absent on runs from before it was recorded. */
  just?: string;
  /** How the branch ended: merged, or discarded. null while it waits. */
  landed: RunLanded | null;
  /** The latest `just rebase` of the branch. null if it was never rebased. */
  rebased: RunRebased | null;
}

/** From the `worktree_rebased` event `just rebase` logs, kept or not. */
export interface RunRebased {
  /** The launch branch it was rebased onto, and the branch's commit before. */
  onto: string;
  old: string;
  /** The branch's commit after the rebase. Absent when it hit a conflict. */
  new?: string;
  /** The adw_quality run that rechecked it, and whether that passed. */
  checks?: string;
  passed?: boolean;
  /** false when worktree_setup failed on the rebased branch. */
  setup?: boolean;
  /** The rebase hit a conflict and was aborted. */
  conflict?: boolean;
  /** The branch moved to `new`. Otherwise it was put back at `old`. */
  kept: boolean;
}

/** From the `worktree_merged` or `worktree_discarded` event `just merge` or `just discard` logs. */
export interface RunLanded {
  state: "merged" | "discarded";
  /** For a merge: the launch branch, fast-forward or merge commit, and its commit after. */
  into?: string;
  how?: string;
  head?: string;
}

/** Why a failed run stopped, from its own stop event or its first failed phase. */
export interface StopReason {
  kind: "call_budget_stop" | "budget_stop" | "live_budget_stop" | "daily_budget_stop" | "machine_budget_stop" | "rate_limit_stop" | "not_accepted" | "phase_failed";
  /** The full message the run printed, git advice included. */
  message: string;
  /** The phase it stopped in, to jump to. */
  phase_id: string | null;
}

/**
 * done_before: done when this run started, so it built nothing.
 * stopped: had phases, but the run ended before its commit without a failure.
 */
export type TicketStatus = "done_before" | "queued" | "running" | "done" | "fail" | "stopped";

export interface TicketRow {
  number: string;
  title: string;
  status: TicketStatus;
  /** Fix passes after a failed verify. */
  retries: number;
  /** What this run's agents spent on the ticket. */
  cost: number;
  /** The ticket's first phase in this run, to jump to. */
  phase_id: string | null;
}

/** An adw_tickets run, its phases grouped by ticket in build order. */
export interface TicketRun {
  feature: string;
  folder: string;
  tickets: TicketRow[];
}

export interface SessionDetail {
  session: Session;
  /** null for a run from before caps were recorded. */
  caps: RunCaps | null;
  /** null unless the run failed. */
  stop: StopReason | null;
  /** null unless this is an adw_tickets run. */
  tickets: TicketRun | null;
  /** null unless the run worked on its own branch (`--worktree`). */
  worktree: RunWorktree | null;
  /** null unless the run is an attempt of a trial, or its panel. */
  trial: RunTrial | null;
  /** Derived from agent_end payloads, so historical runs have it too. */
  usage: SessionUsage;
  /** Ordered by seq. */
  phases: Phase[];
  /**
   * One entry per agent that has run OR is running under this adw_id — lane
   * labels come from here. Finished agents come from the agent_sessions table;
   * an agent still in flight has no row there yet, so its entry is built from
   * its agent_start event (coding_agent is null until it finishes).
   */
  agents: AgentSession[];
}

/**
 * GET /api/sessions/:adw_id/events?after=<rowid>&limit=500
 *
 * Poll with `after` = the cursor from the previous response. `cursor` is the
 * highest rowid in this page (or the `after` you sent, when the page is empty),
 * so it can be fed straight back in. `has_more` means the page hit the limit.
 */
export interface EventsPage {
  events: Event[];
  cursor: number;
  has_more: boolean;
}

/**
 * GET /api/sessions/:adw_id/agents/:agent/prompts
 *
 * The exact compiled prompts sent to an agent, read from
 * `{data_dir}/sessions/{adw_id}/{agent}/prompts/`. These live only as files —
 * the db has no copy. Either field is null when that file isn't on disk, which
 * is the normal state for an agent that never ran in this session, so a 200
 * with two nulls is a valid answer rather than an error.
 */
export interface AgentPrompts {
  system: string | null;
  user: string | null;
}

/** Alias matching the naming of the other endpoint payloads. */
export type PromptsResponse = AgentPrompts;

/** GET /api/sessions/:adw_id/envelopes */
export type EnvelopesResponse = Envelope[];

/** GET /api/sessions/:adw_id/gates */
export type GatesResponse = GateResult[];

/** GET /api/health */
export interface HealthResponse {
  ok: boolean;
  db: string;
  journal_mode: string;
  sessions: number;
}

/** One row on the projects page. available=false: the repo has no runs yet. */
export interface ProjectSummary {
  name: string;
  root: string;
  db: string;
  available: boolean;
  sessions: number;
  running: number;
  last_started_at: string | null;
}

/** GET /api/projects/:project/live: what the live runs spend together against max_live_cost. */
export interface LiveSpend {
  spent: number;
  /** repo.yaml's max_live_cost, null when it's off. */
  cap: number | null;
  /** Each live run's current attempt, largest first. */
  runs: { adw_id: string; cost: number }[];
}

/** GET /api/machine/day: every registered repo's last 24 hours against limits.yaml's max_daily_cost. */
export interface MachineDay extends DaySpend {
  /** Each repo's share, largest first. */
  repos: { name: string; spent: number }[];
  /** Why limits.yaml can't be read; runs stop until it's fixed. */
  error: string | null;
  path: string;
}

/** GET /api/projects/:project/day: the last 24 hours' spend against max_daily_cost. */
export interface DaySpend {
  spent: number;
  /** repo.yaml's max_daily_cost, null when it's off. */
  cap: number | null;
  /** When enough spend ages out to go under the cap, if nothing else runs; null under it. */
  clears_at: string | null;
  since: string;
}

/** GET /api/projects: all=true for `just obs-all`, false for one repo's `just obs`. */
export interface ProjectsResponse {
  all: boolean;
  projects: ProjectSummary[];
}

export interface ApiError {
  error: string;
}

/** One window as Claude Code last reported it. utilization is 0..1. */
export interface LimitWindow {
  utilization: number;
  resets_at: string;
}

/** The newest rate_limit_event any SSSF agent on this machine recorded. */
export interface LimitSample {
  recorded_at: string;
  source: string;
  age_s: number;
  status: string | null;
  overage_status: string | null;
  /** null once that window has reset: the sample no longer says how full it is. */
  five_hour: LimitWindow | null;
  seven_day: LimitWindow | null;
}

export interface SpendRow {
  key: string;
  calls: number;
  est_usd: number;
  output_tokens: number;
  input_tokens: number;
}

/** Spend read from Claude Code transcripts over one window. */
export interface SpendWindow {
  /** five_hour / seven_day: the account's live window. rolling: no live sample, so the last 5 hours or 7 days. */
  basis: "five_hour" | "seven_day" | "rolling";
  start: string;
  end: string;
  calls: number;
  est_usd: number;
  by_kind: SpendRow[];
  by_project: SpendRow[];
  by_model: SpendRow[];
  unpriced_models: string[];
}

/** One Pi agent call from a project's trace db. Cost is Pi's list-price figure. */
export interface PiCall {
  at: string;
  model: string;
  cost: number;
  input_tokens: number;
  output_tokens: number;
}

/** Pi agents' spend over the last 5 hours or 7 days. Pi reports no usage windows. */
export interface PiSpendWindow {
  start: string;
  end: string;
  calls: number;
  est_usd: number;
  by_project: SpendRow[];
  by_model: SpendRow[];
}

/** GET /api/account */
export interface AccountUsage {
  sample: LimitSample | null;
  window: SpendWindow;
  week: SpendWindow;
  transcripts: string;
  /** SSSF's own Pi agents, from every registered project's trace db. */
  pi: { window: PiSpendWindow; week: PiSpendWindow };
}

/** The trial a run belongs to, as an attempt or as its panel (trials.md decision 24). */
export interface RunTrial {
  trial: string;
  role: "attempt" | "panel";
  /** The attempt's roster; null for the panel. */
  roster: string | null;
}

/** One panel reviewer's ruling on one attempt, as trial.py's combine() records it. */
export interface TrialRuling {
  family: string;
  label: string;
  approved: boolean;
  blocking: string[];
  unmet: string[];
}

/** One attempt's row of a trial's scorecard. */
export interface TrialAttemptCard {
  adw_id: string;
  roster: string;
  roster_file: string;
  roster_sha256: string;
  family: string;
  status: SessionStatus | null;
  cost: number;
  tokens: number;
  seconds: number | null;
  /** The last full quality.py check; null before it ran. */
  checks: boolean | null;
  live: boolean;
  /** Lines and files from the trial's base to the branch, once the panel measured them. */
  added?: number;
  removed?: number;
  files?: number;
  /** null when the run itself failed, so the panel never saw it. */
  review: "pass" | "block" | "unknown" | null;
  rulings: TrialRuling[];
  /** Average place across the panel, 1 is best; rank_others leaves out its own family's vote. */
  rank: number | null;
  rank_others: number | null;
  passed: boolean;
}

/** GET /api/projects/:project/trials/:trial */
export interface TrialView {
  trial: string;
  request: string;
  base: string;
  families: string[];
  panel: string | null;
  panel_status: SessionStatus | null;
  panel_cost: number;
  total_cost: number;
  suggested: string | null;
  why: string;
  merged: string | null;
  running: boolean;
  attempts: TrialAttemptCard[];
  /** false until a panel has recorded a scorecard; the rows are then the runs as they are. */
  scored: boolean;
}

/** GET /api/projects/:project/rosters: one row per roster name and file hash, from scored trials. */
export interface RosterRow {
  roster: string;
  sha256: string;
  /** The roster file on disk has this hash. */
  current: boolean;
  trials: number;
  attempts: number;
  passed: number;
  wins: number;
  /** Over the attempts that passed every gate. */
  median_cost: number | null;
  median_seconds: number | null;
}
