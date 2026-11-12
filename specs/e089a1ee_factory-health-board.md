# Plan: the factory page's first release, the health board (piece 4)

Source: `docs/designs/factory-page.md`, sections "What the first release is", "Rows and colours", "How it reads" and "Done". This plan builds piece 4 of its build order. Everything in the design's "Deferred" list is out of scope. So are changes to `adws/check_projects.py`, `adws/phase_times.py` and their `--json`, any guard code, the agentsh policy, `quality.py`, and the docs (piece 5).

All dashboard paths below sit under `.claude/skills/sssf/apps/visualizer/`, written in full from the repo root.

## What the builder must know first

- **Loopback is denied in your shell.** agentsh refuses a connect to `127.0.0.1`, so `.claude/skills/sssf/apps/visualizer/server/listen.test.ts` fails in your shell with "Permission denied". That is expected. The chain's test step runs outside agentsh and is the judge. Do not start the server, and do not `fetch` it in any new test. Test the factory code through its exported functions and its request handler: build a `new Request("http://x/api/factory?listed=1")` and call the handler directly.
- **Run `bun test` and `npx --no-install vue-tsc --noEmit` from the dashboard folder** (`cd .claude/skills/sssf/apps/visualizer`). From the repo root, `bun test` searches the whole hub through the FUSE mount and looks hung.
- **No test may read the real registry, the real traces, the real `/proc` or the real prune status.** Every reader takes its root or path as a parameter, and the tests pass temp dirs: a fake `/proc` tree, seeded sqlite traces, a temp `status.json`, and canned script output from a fake runner. Set `SSSF_REGISTRY` to a temp file in any test that builds a `Projects`. Restore it afterwards, as `server/machine.test.ts` does.
- **Without `--factory`, nothing changes.** Do not register the route, run no command, and leave the `factory` field out of `/api/projects` (or make it `false`). The UI shows no board.
- `server/index.ts` calls `Bun.serve` when it loads, so tests cannot import it. Put all the factory logic, including the handler and the argv parsing, in new modules that the tests import.

## Facts about the data (checked against the hub's trace on 2026-11-11)

- `check_projects.py --json` (run from the hub as `uv run adws/check_projects.py --json`) prints `{skill, hidden, repos: [{repo, branch, checks: [{name, ok, note}], root, stamped, behind, at_stamp, ok}]}`. It **exits 1 whenever any repo fails**, so parse stdout whatever the exit code. Only a parse failure counts as an error. The check names are `repo` (only when the root isn't a git repo), `stamp`, `commit`, `clean`, `skill` and `quality`. The `quality` note is `"<adw_id> <status>"` when a run exists after the update commit. Otherwise it is `"no adw_quality run since <sha>"`, `"no trace"` or `"no update commit"`.
- `phase_times.py --json` prints `{now, days, factor, hidden, repos: [{repo, chains: [...], slow: [...], left_running: [...]}]}` and exits 0. Each `slow` item has `kind` (`"phase"`, `"run"` or `"cost"`), `adw_id`, `chain`, `status`, `start` and `ratio`. Phase items add `name`, `seconds`, `p90_s` and `running`. Run items add `seconds` and `p90_s`. Cost items add `cost` and `p90`. With the default `--factor 2`, every item is already at least 2× its p90.
- Trace tables are `sessions(adw_id, adw_name, status, started_at, ended_at, total_cost, archived)`, `events(adw_id, phase_id, type, name, payload_json, started_at)`, `envelopes(adw_id, output_type, payload_json, created_at)` and `processes(adw_id, kind, pid, host, started_at, ended_at)`. Session status is `success`, `fail` or `running`.
- **A VM's start event is named `sandbox`, not `sandbox_start`.** It comes from `adws/adw_modules/sandbox_remote.py`, with payload `{provider, vm, ...}`. Its end is `sandbox_end` with `{provider, vm, ...}`. A live VM is a `sandbox` event for an `(adw_id, vm)` that has no later `sandbox_end` with the same pair.
- `agentsh_denials` events have `type='log'`, with payload `{agent, denials, notes, polls, pages, slowest_poll_seconds, drops?}`. `drops` is `{type: count}`, `{}` when nothing was dropped, `null` when it wasn't counted, and missing in events from before piece 3.
- Guard findings are `type='log'` events named `bypass_review_<N>` whose payload has numeric keys `bypasses, harms, false_positives, broken_promises, old_holes, blocking` and the boolean `adds_mechanism`. Other `bypass_review_N` log events carry `{message, level}`, so skip any payload without a numeric `blocking`.
- Rule refusals are `type='log'`, `name='rule_refused'`, with payload `{agent, rule, pattern, command}`.
- A spec review's ruling is an envelope with `output_type='ReviewOutput'` whose payload has `approved` (bool). `approved === false` means it blocked.
- A run's stop or breach is an `error` event named `rule_breach`, `permission_breach`, `call_budget_stop`, `rate_limit_stop`, `not_accepted` and the like. Use it only for the row's "what's wrong" text (the latest `error` event's name). The run's `status='fail'` decides the colour.
- `/var/lib/sssf-agentsh-prune/status.json` (written by `.claude/skills/sssf/scripts/agentsh_prune.py`) is `{store_bytes, audit_bytes, last_prune, last_analyze, last_run}`. The byte counts can be `null`, and the times are ISO strings or `null`.
- Each repo's default sandbox is the `sandbox:` key of `<root>/adws/adw_sssf_config/repo.yaml`. Read it with `Bun.YAML.parse`, the way `readRepoCap` in `.claude/skills/sssf/apps/visualizer/server/day.ts` does. For `sandbox: openshell`, an image counts as built when `podman image exists <tag>` exits 0. The tag is the first stdout line of `uv run .claude/skills/sssf/scripts/openshell_image.py --repo <root> --print`, run with cwd = the hub. That is the same check `adws/adw_modules/sandbox_openshell.py` makes.
- The hub's private names live at `<git common dir>/sssf-private-names`, where the common dir comes from `git -C <hub> rev-parse --path-format=absolute --git-common-dir`. The file has one name per line, and blank lines and `#` lines are ignored. A repo is unlisted when the lowercased `"<name>\n<root>"` contains any of the lowercased names, the same rule as `unlisted()` in `adws/check_projects.py`.

## Thresholds (constants in `server/factory.ts`, exported, one block)

```ts
export const GIB = 1024 ** 3;
export const MEM_AMBER = 3 * GIB, MEM_RED = 1.5 * GIB;            // MemAvailable below
export const STORE_AMBER = 15 * GIB, STORE_RED = 25 * GIB;        // store_bytes + audit_bytes at or above
export const PRUNE_AMBER_H = 3, PRUNE_RED_H = 6;                  // last_prune older than
export const POLL_AMBER_S = 1, POLL_RED_S = 3;                    // slowest_poll_seconds above
export const SLOW_AMBER = 2, SLOW_RED = 3;                        // phase/run ratio to p90, at or above
export const COST_AMBER = 2;                                      // cost ratio to p90, at or above
export const RUN_WINDOW_MS = 24 * 3600 * 1000;
export const SCRIPT_TTL_MS = 30_000;
export const CLK_TCK = 100;
export const PRUNE_STATUS = "/var/lib/sssf-agentsh-prune/status.json";
```

## Shared types — `.claude/skills/sssf/apps/visualizer/shared/types.ts`

Add:

```ts
export type FactoryLevel = "red" | "amber" | "ok";
export type FactorySection = "projects" | "machine" | "runs";

export interface LeftoverProcess { pid: number; age_s: number | null; command: string; cwd: string | null; }

export interface FactoryRow {
  section: FactorySection;
  key: string;              // stable id, e.g. "projects/<repo>", "machine/memory", "runs/<repo>/<adw_id>"
  level: FactoryLevel;
  title: string;            // repo name, "Memory", "<repo> <adw_id> <chain>"
  problems: string[];       // what's wrong, one sentence per tripped rule; [] when ok
  figure: string | null;    // the number that tripped it, e.g. "1.2 GiB free", "3.4× its p90"
  next: string | null;      // the next step: a command or which session to ask
  repo: string | null;      // registry name, for the link and the listed-only filter
  adw_id: string | null;
  notes: string[];          // neutral facts: sandbox default, image built, drops not recorded, refusals 0
  processes?: LeftoverProcess[];   // leftover-processes row only
  refusals?: number;               // runs rows: rule_refused count
  guard?: { bypasses: number; blocking: number } | null;  // runs rows with bypass_review findings
}

export interface FactoryBoard {
  generated_at: string;
  hub: string;
  listed: boolean;          // the switch as asked
  private_names: boolean;   // the hub's sssf-private-names file exists
  hidden: number;           // repos the switch hid
  red: number;
  amber: number;
  rows: FactoryRow[];       // red, then amber, then ok; within a level, projects, machine, runs, in source order
  errors: string[];         // a script that failed or printed unparseable output, a trace that would not open
  scripts_at: string | null; // when the cached script results were taken
}
```

Add `factory?: boolean` to `ProjectsResponse`.

## Server

### `.claude/skills/sssf/apps/visualizer/server/factory_machine.ts` (new)

Pure readers. Every function takes its root, path, now and ids as parameters, and none reads the real system by default inside tests.

- `readMeminfo(procRoot: string): { available: number | null }` reads `MemAvailable:` (kB → bytes) from `<procRoot>/meminfo`. If the file is missing or the key is absent, return `null`.
- `memoryRow(available: number | null): FactoryRow` gives red when available < `MEM_RED`, amber when < `MEM_AMBER`, ok otherwise. Null gives amber, "could not read /proc/meminfo". The figure is `"<x.x> GiB free"`, and the next step is "close what holds memory; see the leftover processes row".
- `scanProcesses(procRoot, { uid, selfPid }): ProcInfo[]` lists numeric dirs under `procRoot`. For each it reads `status` (the first number on the `Uid:` line), `cmdline` (NUL-separated, with trailing NULs dropped), `cwd` via `readlinkSync` (null on error) and `stat`. Skip any entry that throws (the process exited or isn't ours), any entry whose uid ≠ `uid`, and `selfPid`. The start time is field 22 of `stat`: split the text after the last `") "` on spaces, then take index 19. The age is `uptime - starttime / CLK_TCK`, where uptime is the first number of `<procRoot>/uptime`. If either can't be read, the age is null.
- `isPytest(argv: string[]): boolean` is true when any argv element's basename is `pytest` or `py.test`, or when `-m` is followed by `pytest`.
- `leftoverRow(procs, { tmpdir, user }): FactoryRow` builds the dirs `<tmpdir>/pytest-of-<user>/` and `/tmp/pytest-of-<user>/`, de-duplicated. A process is a leftover when its cwd, or any argv element, starts with one of those dirs (trailing slash included, or equal to the dir without it). If any process passes `isPytest`, the row is ok, with the note "a pytest run is in progress; not checked". Otherwise any leftover makes it red, with the figure `"<n> processes"`, `processes` listing PID, age and command (the command truncated to 200 characters), and the next step "list them with `ps -o pid,etime,args -p <pids>` and kill them by hand; the page never kills". With no leftovers the row is ok.
- `readPruneStatus(path): {...} | null` and `pruneRows(status, now): FactoryRow[]` produce two rows, "agentsh store" and "agentsh prune". Store: total = `store_bytes + audit_bytes`, giving red at ≥ `STORE_RED` and amber at ≥ `STORE_AMBER`. Both null gives amber, "not measured". Prune: the age of `last_prune` gives red at > 6 h and amber at > 3 h. A null `last_prune` is amber, "never pruned". A missing or unparseable file makes both rows amber, "no prune status at <path>", with the next step `sudo systemctl enable --now sssf-agentsh-prune.timer`.
- `pollRows(perRepo: {repo, events: {at, payload}[]}[]): FactoryRow[]` gives one row per repo that had `agentsh_denials` events in the window. The slowest poll is the max `slowest_poll_seconds`: red when > `POLL_RED_S`, amber when > `POLL_AMBER_S`. Drops: if any event's `drops` is an object with `ptrace_execve > 0`, the row is amber, with the figure "<n> ptrace_execve dropped". When no event in the window has an object `drops` (all are null or missing), add the note "drops: not recorded". Otherwise add a note summing the drops by type. The next step is "ask the hub session to look at agentsh's poll times in `<repo>`'s trace".
- `vmRows(perRepo: {repo, open: {adw_id, vm, provider, at, run_status}[]}[]): FactoryRow[]` gives one row per open VM. It is amber when `run_status !== "running"`, with the problem "VM <vm> on <provider> has no running SSSF run" and the next step "delete it in the <provider> console or CLI, then check `<repo>` run <adw_id>". Otherwise it is ok, with the note "in use by a running run".

### `.claude/skills/sssf/apps/visualizer/server/factory_projects.ts` (new)

- `parseCheckProjects(stdout: string): CheckProjectsJson` throws when the output isn't JSON with a `repos` array.
- `readSandbox(root: string): string` returns the `sandbox:` key of `adws/adw_sssf_config/repo.yaml`, or `"none"` when the key is absent or unreadable.
- `projectRow(repo, { hubSkill, sandbox, image }): FactoryRow` applies these rules, one `problems` entry for each one that trips:
  - Red: a `repo` check that fails (not a git repo). A `clean` check that fails ("dirty tree on <branch>: <note>"; next: "ask the <repo> session to commit or stash its changes"). `at_stamp === false` ("its committed skill doesn't match the hub's at its stamp <stamped>"; next: "ask the <repo> session what changed in `.claude/skills/sssf`"). A `quality` check that fails with a note of the form `<adw_id> <status>` where status ≠ `success` and ≠ `running` ("last quality run <adw_id> failed"; next: "ask the <repo> session to look at run <adw_id>"). Set `adw_id` from that note too.
  - Amber: `behind === true` ("skill <stamped> is behind the hub's <skill>"; next: "ask the <repo> session to update to skill <skill>"). A `quality` check whose note starts with `no ` ("no quality run has passed since its last update"; next: "ask the <repo> session to run `just quality`"). A failing `commit` check when not behind ("the update commit doesn't name skill <skill>"). `at_stamp === null` while behind becomes the note "the hub doesn't know its stamp commit".
  - Notes: `sandbox: <value>` always. For openshell, also `image built` or `image not built` (amber when not built: "its OpenShell image isn't built"; next: "ask the engineer to run `just openshell-setup` in <repo>"). When the image check itself fails, the note reads "image: not checked (<reason>)".
- The openshell image check goes through the injected runner (below), is cached with the scripts, and runs only for repos whose sandbox is `openshell`.

### `.claude/skills/sssf/apps/visualizer/server/factory_runs.ts` (new)

- `parsePhaseTimes(stdout)` works like `parseCheckProjects`.
- `runRows(perRepo: {repo, runs: FactoryRunFacts[]}[], slow: PhaseTimesJson): FactoryRow[]`, where `FactoryRunFacts` is `{adw_id, adw_name, status, started_at, ended_at, last_error: string | null, live: boolean | null, refusals: number, guard: {bypasses, blocking} | null, spec_blocked: boolean}`. There is one row per run, titled `"<repo> <adw_id> <adw_name>"`:
  - Red: `status === "fail"` ("failed" plus ` (<last_error>)` when known). `status === "running" && live === false` ("marked running but no live process: it crashed"). `guard.blocking > 0` or `guard.bypasses > 0` ("guard found <b> blocking, <n> bypasses"). A `slow` item of kind `phase` or `run` for this repo and adw_id with ratio ≥ `SLOW_RED` ("phase <name> took <ratio>× its usual top").
  - Amber: a phase or run item with ratio ≥ `SLOW_AMBER`. `refusals > 0` ("<n> rule refusals"). `spec_blocked` ("a spec review blocked"). A `cost` item with ratio ≥ `COST_AMBER` ("cost $<cost>, <ratio>× its usual").
  - Set `refusals` and `guard` on every row, so the API shows each day's guard runs with their refusals. The next step for any red or amber row is "open the run: #/<repo>/<adw_id>, or ask the <repo> session".

### `.claude/skills/sssf/apps/visualizer/server/db.ts` (edit: add read-only queries to `SssfDb`)

Each query guards against older traces the way `liveRows` does: on a missing table, return `[]` or `null`.

- `factoryRuns(since: string)` returns sessions with `archived = 0` and (`status = 'running'` or `started_at >= since` or `ended_at >= since`). It joins: the latest `error` event name; whether an open `processes` row exists (`kind='adw' AND ended_at IS NULL`), which is `null` when the `processes` table is missing; the `rule_refused` count; the sums of `bypasses` and `blocking` over `bypass_review_%` log events with a numeric `$.blocking` (`json_type(payload_json,'$.blocking') IN ('integer','real')`), or null when there are none; and whether any `ReviewOutput` envelope has `json_extract(payload_json,'$.approved') = 0`.
- `agentshPolls(since: string): {at, payload_json}[]` reads `name='agentsh_denials'` events since `since`.
- `openSandboxes(): {adw_id, vm, provider, at, run_status}[]` returns `name='sandbox'` events with no later `sandbox_end` for the same `adw_id` and `json_extract(...,'$.vm')`, joined to `sessions.status`.

For the crash check in `factory.ts`: a run counts as live when an open `processes` row exists, and that row's host is null or equals `os.hostname()`, and `<procRoot>/<pid>` exists. The live check uses the board's `procRoot`, so tests stay on the fake `/proc`. When the trace has no `processes` table, `live` is `null`, and the row isn't judged a crash.

### `.claude/skills/sssf/apps/visualizer/server/factory.ts` (new)

- The thresholds block above.
- `factoryHub(argv: string[]): string | null` returns the resolved path after `--factory`. When the flag has no value, it throws `"--factory needs the hub's path"`.
- `type Runner = (cmd: string[], cwd: string) => Promise<{ code: number; stdout: string; stderr: string }>`, with `bunRunner` (a `Bun.spawn` wrapper with a 60 s timeout) as the default used only by `index.ts`.
- `class Factory`, constructed as `new Factory({ hub, projects, runner, procRoot = "/proc", pruneStatus = PRUNE_STATUS, uid = process.getuid?.() ?? -1, selfPid = process.pid, tmpdir = process.env.TMPDIR ?? "/tmp", user = process.env.USER ?? os.userInfo().username, hostname = os.hostname(), now = () => Date.now() })`:
  - `scripts(refresh: boolean)` runs `uv run adws/check_projects.py --json`, `uv run adws/phase_times.py --json`, the git common-dir lookup and each openshell repo's image check in the hub. The results are cached for `SCRIPT_TTL_MS`, and `refresh` drops the cache. Concurrent callers share one in-flight promise. A script failure (it couldn't start, or its stdout won't parse) adds to `errors`, with the stderr tail cut to 300 characters, plus a red row in that script's section ("`check_projects.py --json` failed", next: "run `just projects` in the hub"). It never throws.
  - `board({ listed, refresh }): Promise<FactoryBoard>` builds the projects and runs rows from the cached scripts plus the traces. The projects and runs sections are cached together for 30 s (the design's refresh pace), unfiltered, each row carrying its `repo`. The machine rows are rebuilt on every call (memory, leftovers, the store, the prune, polls and VMs). Polls and VMs read every project in `projects.everyProject()` via `projects.db(p)`. Then it applies `listed`: when true, it drops every row whose `repo` is unlisted, counts the distinct unlisted repos it dropped as `hidden`, and leaves machine rows without a repo in place. It sorts the rows red, amber, ok (a stable sort keeps the section order projects, machine, runs) and counts `red` and `amber`.
  - When `listed` is asked for and the private-names file doesn't exist, it refuses, as the scripts do. `board` throws a `FactoryError` with status 409 and the message "the hub has no .git/sssf-private-names, so the switch can't hide anything".
- `factoryHandler(factory: Factory): (req: Request) => Promise<Response>` handles `GET /api/factory?listed=1&refresh=1`. It returns the JSON board, the 409 above as `{error}`, or `{error}` with 500 on anything else. Headers match `json()` in `index.ts` (`no-store`).

### `.claude/skills/sssf/apps/visualizer/server/index.ts` (edit)

- Parse `const FACTORY_HUB = factoryHub(Bun.argv)` near `ALL`. `--factory` implies the registry listing: use `ALL || FACTORY_HUB !== null` wherever `ALL` decides `new Projects(null)`.
- Only when `FACTORY_HUB` is set, create `new Factory({ hub: FACTORY_HUB, projects, runner: bunRunner })` and add the route `"/api/factory": factoryHandler(factory)` (spread it conditionally into `routes`). Without the flag, `/api/factory` falls through to the existing `notFound("no route ...")`.
- Add `factory: FACTORY_HUB !== null` to the `/api/projects` response.
- At startup, under the flag, log `[sssf] factory board    <hub>`.
- Update the header docstring's usage lines with `bun run server/index.ts --factory /path/to/hub   # the hub's health board on #/ (implies --all)`. Say that this is the one mode that runs commands: the hub's two `--json` scripts, git, and the OpenShell image check.

## UI

### `.claude/skills/sssf/apps/visualizer/src/lib/api.ts` (edit)

Add `fetchFactory(listed: boolean, refresh = false): Promise<FactoryBoard>` against `/api/factory?listed=<0|1>&refresh=<0|1>`, using the file's existing fetch helper. On a non-OK response, throw with the body's `error`.

### `.claude/skills/sssf/apps/visualizer/src/components/FactoryBoard.vue` (new)

- Poll `fetchFactory` every 10 s, as `ProjectsList.vue` does with `setInterval` and `onUnmounted`. Add a "refresh now" button that calls it with `refresh=true`. The button is the page's only control besides the switch, and it changes nothing on disk.
- Add a "listed only" checkbox, kept in `localStorage` (`sssf.factory.listed`). Disable it, with a tooltip, when `private_names` is false. When it's on, show "<hidden> unlisted repos hidden".
- Show a summary strip with the red count, the amber count, `generated_at` and `scripts_at`, plus `errors` when there are any.
- One list of rows in API order. Each row shows a coloured level dot (red uses `var(--red)` if `style.css` defines it, amber uses an amber or `--yellow` variable, ok uses `var(--green)`; check `src/style.css` and add the missing variables there), a section chip, and the title. When `repo` is set and `adw_id` isn't, the title links to `#/<repo>` via `projectHref`. When both are set, it links to `#/<repo>/<adw_id>`, built with `encodeURIComponent`, because `hrefFor` is relative to the current route. After the title come the `problems`, the `figure`, `next` (in monospace when it is a command), the `notes` dimmed, the `processes` as a small PID/age/command table, and `refusals`/`guard` for runs.
- Tab title: while mounted, set `document.title` to `"(<red>) SSSF factory"` when red > 0, or `"SSSF factory"` otherwise. Restore the previous title in `onUnmounted`.

### `.claude/skills/sssf/apps/visualizer/src/App.vue` (edit)

Keep `factory` from `fetchProjects()` (`listing.factory === true`). On the landing route (`!route.project && all`), render `<FactoryBoard v-if="factory" />` above the unchanged `<ProjectsList />`. Without the flag the template renders exactly as now.

## Hub recipe

### `local.just` (edit)

Add:

```just
# the hub's health board across every repo and this machine, read-only: just factory
factory:
    cd .claude/skills/sssf/apps/visualizer && bun install --backend=copyfile --silent && bunx vite build --logLevel warn && bun run server/index.ts --factory "{{justfile_directory()}}"
```

## Tests (all new, in `.claude/skills/sssf/apps/visualizer/server/`)

Write each test with `mkdtempSync` fixtures and `afterEach` cleanup, as in `server/machine.test.ts`, and with a fixed `NOW`. The design's "every colour rule" needs at least one test per bullet below, covering each colour each rule can give, plus the ok case.

- `factory_machine.test.ts` uses a fake `/proc` built in a temp dir: `meminfo`, `uptime`, and `<pid>/{status,cmdline,stat}` with `cwd` as a symlink to a temp dir.
  - Memory: 4 GiB is ok, 2 GiB amber, 1 GiB red, a missing meminfo amber.
  - Leftovers: one process whose cwd is under `<tmp>/pytest-of-<user>/` and one whose argv names `/tmp/pytest-of-<user>/x` give red, with both PIDs and ages computed from `stat` and `uptime`. Another uid's process is ignored. `selfPid` is ignored. When a pytest process (`python -m pytest`) is present, the row is ok with "in progress". With none, ok. An unreadable entry is skipped.
  - Store: 10 GiB is ok, 16 GiB amber, 26 GiB red, both null amber. Prune: 1 h is ok, 4 h amber, 7 h red, a null `last_prune` amber, a missing file amber for both rows.
  - Polls: 0.5 s is ok, 1.5 s amber, 3.5 s red. `drops: {ptrace_execve: 2}` is amber. `drops: {}` is ok. All null or missing gives the note "drops: not recorded".
  - VMs: open with its run running is ok, open with its run `fail` amber.
- `factory_projects.test.ts` feeds canned `check_projects.py --json` output.
  - Red: dirty, `at_stamp: false`, quality `"abc123 fail"`, and the `repo` check failing.
  - Amber: behind, a quality note starting `no adw_quality run since`, and openshell with no image.
  - Ok: a fully passing repo, with its `sandbox:` read from a temp `repo.yaml`.
  - A quality note of `"<id> running"` isn't red.
- `factory_runs.test.ts` uses seeded sqlite traces (create the tables above with `bun:sqlite`, as `server/db.test.ts` does) through `SssfDb.factoryRuns`, `agentshPolls` and `openSandboxes`, plus canned `phase_times.py --json`.
  - Red: a failed run with a `rule_breach` error, a running run with no open process, a guard run with `blocking: 1`, a guard run with `bypasses: 1`, and a phase at ratio 3.2.
  - Amber: ratio 2.4, one `rule_refused`, a `ReviewOutput` with `approved: false`, and a cost ratio of 2.5.
  - Ok: a clean success. An archived run is left out, and so is a run older than 24 h.
  - A trace without a `processes` table doesn't crash and doesn't judge a crash.
  - `bypass_review_1` message events (with no numeric `blocking`) are ignored.
- `factory.test.ts` drives `Factory` and `factoryHandler` with a fake runner, a fake proc root, a temp prune status and a temp registry (`SSSF_REGISTRY`) pointing at temp repos with seeded traces. The fake runner keys on `cmd.join(" ")` and counts calls.
  - The rows come back sorted red, amber, ok, and `red` and `amber` count correctly.
  - `?listed=1` hides a repo named in the fake private-names file (the fake runner answers the `git rev-parse --git-common-dir` call with a temp dir holding `sssf-private-names`), drops its project, run and VM rows, and sets `hidden: 1`.
  - `?listed=1` with no names file gives 409.
  - A second call within 30 s doesn't rerun the scripts, `refresh=1` does, and a call after `now` moves 31 s on does.
  - `check_projects.py` exiting 1 with valid JSON still yields rows.
  - Unparseable output yields `errors` and a red error row, not a 500.
  - `factoryHub(["--factory", "/h"])` returns `/h`, `factoryHub([])` returns null, and `factoryHub(["--factory"])` throws.
- `listen.test.ts` (edit; it fails in your shell and the chain's test step runs it). In the existing test, also assert that, started without `--factory`, `GET /api/factory` answers 404 and `/api/projects` has `factory: false`. Don't add a `--factory` socket test.

## Docs this change makes out of date

- The `server/index.ts` header docstring gets the new usage line (above).
- The `local.just` recipe comment is the recipe's help text (above).
- `docs/designs/factory-page.md`, `docs/open.md` and the hub docs (`docs/sssf.md`, which lists the `local.just` commands) are piece 5 and are out of scope here. The documenter should note that `docs/sssf.md` doesn't yet list `just factory`, that the design's build-order entry 4 still reads "Not started", and that the design says `sandbox_start` where the trace's event is named `sandbox`.
- The repo keeps no CHANGELOG.
- No `app_docs/` write-up describes the dashboard's landing page in a way this contradicts, beyond the absence of the board. The documenter should check any write-up about `just obs-all`'s landing page.

## Verification

Run from `.claude/skills/sssf/apps/visualizer`:

1. `bun test` passes, apart from `server/listen.test.ts`, which fails in the builder's shell with "Permission denied" because loopback is denied. The chain's test step runs it outside agentsh, and it must pass there.
2. `npx --no-install vue-tsc --noEmit` exits 0.
3. `bunx vite build --logLevel warn` exits 0.

Run from the repo root:

4. `just --show factory` exits 0.
5. Grep shows that no new test names `/proc` other than through a temp root, `/var/lib/sssf-agentsh-prune`, or `~/.config/sssf` (the reviewer checks this).

The engineer's real check from the hub, after merge and outside the chain: run `just factory` and read `/api/factory` and `/api/factory?listed=1`. Every registered repo should appear, the switch should hide the unlisted ones, and the night's prune status and the day's guard runs with their refusals should be there. That check is the second half of the design's "Done" and isn't the builder's.
