# Observability reference

The event schema, the seven SQLite tables, and the polling contract. The one data path is **agents → sqlite → web ui**.

## Two stores, one truth

**Files are the raw record** (`raw_output.jsonl` streams, `envelope.json`, `agent_map.json`); **SQLite (`sssf.db`) is the queryable mirror** the UI reads. `tracer.py` writes both. Losing the db loses nothing that can't be rebuilt from files.

Location comes from `observability.db` in `sssf.config.yaml`, default `adws/adw_data/sssf.db`, inside the **target** repo, gitignored.

## Event schema

`tracer.py` emits these types, every one logged against its `adw_id` **and** `phase_id`:

| Type | Emitted when |
|---|---|
| `phase_start` | a `run.phase(...)` block is entered |
| `agent_start` | a coding agent is spawned or resumed for `ph.call(...)` |
| `tool_call` | a tool returns, under the harness's own name: pi's `read`, `bash`, `edit`, `write`, or Claude Code's `Read`, `Bash`, `Edit`, `Grep` and the rest. **One event per real call**, named `bash: ls -la src`, payload `{tool, tool_call_id, args, result_snippet, ok, duration_ms, agent}` |
| `handoff` | an envelope crosses from one agent to the next |
| `gate_pass` | a gate found no failed checks; the payload carries `attempt`, `checks` (the evidence), and an empty `violations` |
| `gate_fail` | a gate found at least one failed check; the payload carries `attempt`, `checks`, and `violations` |
| `log` | an explicit `ph.log(...)` from the ADW script |
| `agent_end` | the agent's run completes; envelope parsed or not; the payload carries `cost`, `usage` (the per-component breakdown), `context_tokens`, `context_window` |
| `phase_end` | the block exits; carries the resolved status |
| `error` | a raise inside a phase block |

`parent_id` nests spans, so an agent phase expands into its tool-call spans in the UI.

**Spend is itemised per phase.** `agent_end.usage` carries tokens *and* dollars for each component pi reports: `input`, `output`, `cache_read` and `cache_write`, summed across every send the phase made, so a phase that retried on a bad envelope or a failed gate shows what all its attempts cost, not just the last one. The four components sum to `total_tokens`, and their costs sum to `total_cost`; the visualizer's Cost panel renders them as a table you can add up by eye.

`reasoning_tokens` is the thinking share and is **inside** `output_tokens`, not a fifth component. Measured across every session on disk, reasoning never exceeds output and the four components always reconcile to the total. It bills at the output rate, so the panel nests it under output rather than adding it. Runs predating the breakdown have no `usage` key at all; the lump `cost` and the event's own `tokens` still stand, and the UI says so rather than rendering zeroes.

**Context is occupancy, not spend.** `events.tokens` and `sessions.total_tokens` bill every turn, so they only grow. An agent that burned 100k tokens may be sitting in a 15k window. `context_tokens` is how full the window actually was when the agent stopped, which is what the visualizer's per-lane Context bar measures against `context_window`.

It is computed the way pi computes it for its own footer and its auto-compaction trigger (`calculateContextTokens` in the coding agent's `core/compaction/compaction.ts`): take the last *valid* assistant turn, skipping `aborted` and `error` turns, and read `usage.totalTokens`, falling back to `input + output + cacheRead + cacheWrite`. Cache reads count; cached prompt is still prompt. `context_window` is the same `contextWindow` pi reads from `~/.pi/agent/models.json`, so `context_tokens / context_window` is the number pi would show. Both are NULL on rows written before the columns existed, and the lane draws no bar rather than a misleading empty one.

Two caveats worth knowing. Pi adds an *estimate* for any messages trailing the last assistant usage; in a batch (`-p`) run the session ends on that message, so the two agree. And if auto-compaction fires as the very last act of a run, the recorded number is the pre-compaction size. Pi itself reports `null` in that window rather than guessing.

**Gates record evidence, not just a verdict.** A gate returns one `{item, ok, note}` check per thing it looked at, and `violations` are derived from the failed ones. Both land in `gate_results` (`checks_json` + `violations_json`) and in the `gate_pass`/`gate_fail` payload, so a green gate can answer *what did you verify*, such as `{"item": "…/plan.md", "ok": true, "note": "exists, 454B"}`, rather than only *did it pass*. Rows written before this existed have `checks_json` NULL; treat that as "no evidence recorded", not "nothing checked".

The gate event payload carries `attempt` too, so the `gate_results` table and the event stream are equivalent sources: a live consumer can group gate results per correction round from events alone, without a second query.

**A `tool_call` is the one event that spans time**, so it fills both `started_at` and `ended_at` on the row: the tool's real start and return. Every other type is a point in time: `started_at` is when it was recorded and `ended_at` stays NULL. Lay tool calls out on a time axis from those columns, never by parsing `payload_json` (`duration_ms` is in the payload too, as pi's own number, but it is a convenience, not the source for layout).

**Streaming is solved by construction.** `agent_pi.py` and `agent_cc.py` each tail their agent's JSONL stdout line by line, and fold it into one `tool_call` record of the same shape per call. The tracer inserts each event into `sssf.db` **while the agent is still working**, never batched at phase end. The first smoke run showed tool calls mid-run. Everything downstream is a poll → render.

## Tables

```sql
sessions (
  adw_id        TEXT PRIMARY KEY,
  adw_name      TEXT,              -- ADW script(s) run, e.g. "adw_plan + adw_build_test"
  request       TEXT,              -- the engineer's ask
  status        TEXT,              -- running | success | fail
  engineer      TEXT,
  started_at    TEXT, ended_at TEXT,
  total_tokens  INTEGER, total_cost REAL,
  archived      INTEGER DEFAULT 0  -- review triage, set by the UI, never by a run
);

phases (
  phase_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  seq           INTEGER,
  name TEXT, kind TEXT, owner TEXT, description TEXT,
  status        TEXT DEFAULT 'fail',   -- success must be earned
  attempt       INTEGER DEFAULT 0, retries INTEGER DEFAULT 0,
  error         TEXT,
  started_at    TEXT, ended_at TEXT
);

events (
  event_id      TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,   -- every event logs against adw + phase
  parent_id     TEXT,                     -- span nesting
  type          TEXT,   -- phase_start | phase_end | agent_start | agent_end | tool_call
                        -- | handoff | gate_pass | gate_fail | log | error
  name          TEXT,
  payload_json  TEXT,
  tokens        INTEGER,
  started_at    TEXT, ended_at TEXT   -- ended_at set only on events that span time
);

envelopes (
  envelope_id   TEXT PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  agent         TEXT,
  output_type   TEXT,              -- name of the data_types model it parsed against
  payload_json  TEXT,
  valid         INTEGER,
  attempt       INTEGER,
  created_at    TEXT
);

gate_results (
  id            INTEGER PRIMARY KEY,
  adw_id        TEXT REFERENCES sessions,
  phase_id      TEXT REFERENCES phases,
  attempt       INTEGER,
  gate          TEXT,
  passed        INTEGER,
  violations_json TEXT,             -- derived: the failed checks, as "item: note"
  checks_json   TEXT,               -- [{item, ok, note}]: everything the gate looked at
  created_at    TEXT
);

processes (                        -- adw_id → pid, so a stuck run can be stopped
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  adw_id        TEXT REFERENCES sessions,
  kind          TEXT,               -- 'adw' (the workflow process) | 'agent' (a coding-agent child)
  name          TEXT,               -- '' for the adw, the agent name for a child
  pid           INTEGER,
  command       TEXT,               -- what the pid WAS; pids get recycled, so verify before killing
  started_at    TEXT, ended_at TEXT, -- ended_at NULL = believed alive
  host          TEXT                -- the pid only means something on this host
);

agent_sessions (                   -- the queryable mirror of agent_map.json
  adw_id        TEXT REFERENCES sessions,
  agent         TEXT,
  coding_agent  TEXT, model TEXT, color TEXT,   -- color: the config's lane swatch
  session_id    TEXT,
  context_tokens INTEGER,           -- window occupancy after the agent's last turn
  context_window INTEGER,           -- the model's ceiling, from the pi registry
  created_at    TEXT, last_used_at TEXT,
  PRIMARY KEY (adw_id, agent)
);
```

**A hung agent emits nothing**, which is exactly when you need its pid: no events, no tokens, no output to read. `processes` is the only table that can answer "what is this run running, and how do I stop it". `just procs <adw_id>` lists what is live, and `just kill <adw_id>` stops the chain first, so a retry can't start, then its agents, and both verify the recorded `command` still matches the pid before signalling it. A killed run finalizes its own trace: SIGTERM and SIGINT are turned into `SystemExit` in `session.ensure`, so the session lands on `fail` with its process rows closed instead of reading `running` forever.

**Derived, never stored:** phase durations (`ended_at − started_at`), session phase-progress (query `phases` by `adw_id`), lane layout (`kind` + `owner`).

Phase status invariants: `queued` only for manifest-declared phases not yet entered (dashed in the UI); `running` on enter; only a clean exit writes `success`. Agent phases additionally need the envelope parsed and gates green; everything else resolves to `fail`.

## WAL pragmas

Open **every** connection, writer and reader, with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=30000;   -- tracer.py; the visualizer's connections use 5000
```

The tracer waits longer because parallel worktree runs share the db, and a phase should wait out another run's write rather than fail.

WAL allows readers during writes. Writers are the tracers of running ADW processes; concurrent writers are fine given one small transaction per event plus `busy_timeout`. The visualizer reads on a readonly connection with exactly one exception: archiving a session (`POST /api/projects/:project/sessions/:adw_id/archive`) opens a second connection to set `sessions.archived`. That flag is review triage, saying a human has looked at the run, so it is the reader's state living on the row, and no tracer ever writes or reads it.

## Polling contract

**The UI never receives pushes.** No ingest endpoint, no WebSocket, no backfill or dedup logic.

Live view polls on a rowid cursor every 500 ms. The interval is fixed in the UI. `observability.poll_ms` has no effect.

```sql
SELECT ... FROM events WHERE adw_id = ? AND rowid > ? ORDER BY rowid LIMIT 500;
```

Keep the highest `rowid` returned as the next cursor. History is **the same queries** with filters, lazy-paged as the engineer scrolls or drills in. One mechanism serves both live and past runs, which is why there is no separate replay path.

## Dashboard endpoints

`apps/visualizer/server/index.ts` serves these. Anything else outside `/api/` gets the built UI.

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `ok`, and whether the server shows every registered repo |
| `GET /api/projects` | each registered repo, its db, and its session counts |
| `GET /api/account` | the Claude account's usage windows and where its spend went, plus pi and ChatGPT use |
| `GET /api/machine/day` | every repo's last 24 hours against `max_daily_cost` in `~/.config/sssf/limits.yaml` |
| `GET /api/machine/sandbox` | what VM runs used of each provider's free allowance |
| `GET /api/factory` | only when the server is started with `--factory <hub>`: the hub's health board, every registered repo and the machine, red rows first. `?listed=1` drops every row of a repo in the hub's `.git/sssf-private-names`; `?refresh=1` reruns the hub's `check_projects.py --json` and `phase_times.py --json` instead of using the 30-second cache |
| `GET /api/projects/:project/health` | the db path, journal mode and session count |
| `GET /api/projects/:project/day` | the last 24 hours against `repo.yaml`'s `max_daily_cost` |
| `GET /api/projects/:project/live` | what live runs spend together against `max_live_cost` |
| `GET /api/projects/:project/trials/:trial` | a trial's attempts and the panel's scorecard |
| `GET /api/projects/:project/rosters` | every scored trial, per roster file |
| `GET /api/projects/:project/sessions` | session summaries, `?limit=` default 200 |
| `GET /api/projects/:project/sessions/:adw_id` | one session's detail |
| `POST /api/projects/:project/sessions/:adw_id/archive` | sets `archived`, body `{"archived": false}` to undo |
| `GET /api/projects/:project/sessions/:adw_id/events` | events after `?after=` rowid, `?limit=` default 500 |
| `GET /api/projects/:project/sessions/:adw_id/envelopes` | the session's envelopes |
| `GET /api/projects/:project/sessions/:adw_id/gates` | the session's gate results |
| `GET /api/projects/:project/sessions/:adw_id/agents/:agent/prompts` | the prompts the agent was sent, read from the session dir |

## Dashboard environment

| Variable | Effect |
|---|---|
| `SSSF_DB` | the trace db when `--db` isn't passed |
| `SSSF_OBS_PORT`, `PORT` | pin the port, and a taken pinned port is an error. Unset, the server takes the first free port from 4600. The server listens on `127.0.0.1` only. |
| `SSSF_REGISTRY` | the project registry file. Default `$XDG_CONFIG_HOME/sssf/projects.json`, or `~/.config/sssf/projects.json`. |
| `SSSF_CODEX_LIMITS` | `1` reads the ChatGPT plan's usage windows through `codex app-server`. Off by default. |
| `CLAUDE_CONFIG_DIR` | where Claude Code's transcripts are read, `<dir>/projects`. Default `~/.claude`. |
| `PI_CODING_AGENT_DIR` | where pi's sessions are read, `<dir>/sessions`. Default `~/.pi/agent`. |
