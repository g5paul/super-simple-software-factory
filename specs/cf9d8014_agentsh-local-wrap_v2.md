# Run local agents under agentsh when a repo asks for it

Rollout step 1 of `docs/designs/agentsh-local-sandbox.md`. Its Decisions table is settled. This is the second version of the plan: it answers `spec_review_1.md`.

## Threat model

The agent runs as the engineer's user. It can run anything, spell it any way, and change whatever the policy lets it write. agentsh checks real execs, opens and connects, and the agent can't reach the root-owned config or the service.

What the agent can write matters to how SSSF decides to wrap:

- **On a run without `--worktree`, the main checkout is the workspace.** `sssf-workspace` allows writing `adws/adw_sssf_config/repo.yaml`, including `local_guard: text`, and SSSF's own files there.
- **On a `--worktree` run, the worktree's `.git` file is agent-writable** under `${PROJECT_ROOT}/**`. An agent can repoint it, so anything that asks git from the worktree can get an answer the agent chose. `worktree.main_checkout()` does that.

So a running process **never re-reads** `local_guard`, the main checkout or the git common dir. It reads them once in `session.ensure`, before `worktree.enter` moves the process and before any of its agents has run, then carries them on the agent request. `start` makes no git call and reads no `repo.yaml`.

What this guard can't stop at its level:

- A later process, such as a new chain or a resume, reads `repo.yaml` afresh. On a run without `--worktree`, an edit an earlier agent made there is caught only by the after-call `protected_files` check. The same holds for a chain launched from inside a worktree, which trusts that worktree's `.git` file, as `worktree.enter` does on the baseline.
- A copied binary under a new name, and anything quality checks or git do outside the wrap.

## Baseline

`main` at `b208789`: the text guard alone. Every finding is judged against it.

## Bar

**Text.** With `local_guard: text` or no setting, every command, env var and refusal is byte for byte as baseline.

**Wrapping.**

- With `agentsh`, no agent process runs outside the wrap or under a policy other than the generated one. Nothing the text guard stops gets through because of this change.
- A missing carried guard value refuses. It never means `text`.
- No state lets a run go on after the service or the attach has failed.
- No generated policy lets an agent read or change what `docs/designs/agentsh/deny-probe.sh` shows held.

**Refusing.** A wrong setting, stale policy or missing service stops the run with a message naming the sudo step. It never hangs and is never a breach.

**Stopping.** A silence stop, `stop_tree`, `just kill` and a SIGTERM to the run each end the wrap and everything under it, and leave no agentsh session open. A process that detaches from the tree stays the existing left-behind limit.

**Denial counting.**

- Only a `ptrace_execve` event with decision `deny` and a non-empty rule counts. File, network and unnamed denials never count, and go to the trace with their paths.
- Each counted denial adds one to the same tally as the text guard's counted refusals. The tally is per send, as the forwarder's is today. Built-in secret-file refusals still don't count.
- While the tally is below `rules.MAX_REFUSALS` the agent goes on: the first two denials don't stop the run.
- When the tally reaches three, SSSF stops the agent and raises the existing three-refusal stop, `rules.RuleBreach` with `refusals=n`, which says "Nothing refused ran". That is what "never a breach" means here: a denied exec never takes the `refusals=0` path that says the command may have run. It records the same `rule_breach` trace event, with `refusals` set, as the text guard's three-refusal stop does on the baseline.
- A count reached only in the final read after the agent exits raises the same stop, so the outcome doesn't depend on poll timing.

**Spend.** A call stopped this way keeps its spend: the stop carries `partial`, as a stream stop does.

**Default bar.** No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise.

## Out of scope

Holes this change leaves open, to be filed as old holes:

- The request's accepted limits: a copied binary, an agent reading its own sign-in, Claude Code writing `~/.claude.json` and reading other projects' transcripts, shared `/tmp`, the server API open to unwrapped local processes, quality checks outside the wrap.
- `config.md` limits this change touches and doesn't close:
  - **Forged stream events** through `/proc/$PPID/fd/1`, since `sssf-fd-leaves` allows pipe descriptors. A forged `agentsh:` line is dropped, and attach reads stderr only, first match wins.
  - **Git data writable under `sssf-git-data`:** `info/`, `config.worktree`, `commondir` and `sssf-private-names`. The rule set is kept as tested, and `permissions.py`'s after-call check still covers them.
  - **A left-behind process** that detaches from the tree. It stays traced by the server, and `stop_tree` can't find it through `ps`.
  - **`secret_files`' name-matching limits:** a copy or rename to an unprotected name inside the workspace. The policy matches names and paths, as the guard does.
- A later process re-reading `repo.yaml`, as the Threat model says.
- agentsh's own bugs, the pi hang, turning this on anywhere, patching or removing text checkers, `--sandbox` providers, sudo, and running a real agent.
- `claude auth status`, `pi --list-models` and `pi auth print-bearer-token` are SSSF's own control calls, take no prompt, and stay outside the wrap.

## Files and changes

All modules below are under `.claude/skills/sssf/templates/adws/adw_modules/`. Don't edit the stamped `adws/adw_modules/`.

### `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

Add to `RepoSettings`:

- `local_guard: Literal["text", "agentsh"] = "text"`. A model validator raises when it is `agentsh` and `sandbox != "none"`.
- `deny_exec: list[DenyExec]`, with `DenyExec` using `extra="forbid"`:
  - `name`: slug matching `^[a-z0-9-]+$`, unique, not a base rule name.
  - `command`: a bare name with no `/`, because rules match a basename.
  - `args`: optional Go regex. Must compile with `re`, and reject `(?<`, `(?=`, `(?!` and backreferences, which RE2 lacks.
  - `reason`: required, emitted as a comment.
- `network_allow: list[str]`: bare host names. Reject a scheme, `/`, a port, an IP literal, `localhost`, `*`, and any wildcard other than `*.a.b`.

### `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

Add a frozen `LocalGuard(BaseModel)` with `mode: Literal["text", "agentsh"]`, `root: str = ""`, and `policies: dict[str, Policy] = {}`, keyed by harness, where `Policy` holds `name` and `text`. Add `local_guard: Optional[LocalGuard] = None` to `AgentRequest`. `None` refuses at launch on a local tree. Only an explicit `mode="text"` means text.

### `.claude/skills/sssf/templates/adws/adw_modules/runner.py`

`Run.__init__` sets `self.local_guard = None`. `session.ensure` fills it. `Run(` is built only in `session._start`, which only `ensure` calls, and `runner.py` is the only caller of `agents.execute`, so every chain gets it this way. That includes the hub's `adws/adw_guard_change.py`.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` (new)

Constants, which tests monkeypatch: `BINARY=/usr/bin/agentsh`, `CONFIG=/etc/agentsh/sssf.yaml`, `POLICY_DIR=/etc/agentsh/policies`, `SERVER=unix:///run/agentsh/agentsh.sock`, `API=http://127.0.0.1:18080`, `PINNED_SHA256=3774b1170d447e8c935fb8c2d7421199b6d3fafc312d6f77de1bb2cd1c5db45d` (build `41960715`), `ATTACH_SECONDS=30`, `PAGE=5000`, `POLL_SECONDS=5`, `OVERLAP=500`.

**Generation.** `policy_name(main, harness)` returns `sssf-<slug of main.name>-{claude|pi}`. `policy_text(settings, git_common_dir, harness)` is a port of `deny-policy.py` and `sssf-net.yaml`, with their constants, comments, rule order and reasons kept verbatim, since `docs/` doesn't ship. It uses the same `rule()` format so output stays byte-comparable, but doubles any `'` in a path so a value can't break out of its YAML quote. Additions:

- Git common dir: passed in, absolute. The same in every worktree, so the policy is per repo.
- `secret_files`: **a rule `sssf-repo-secret-files` goes first, before `sssf-env-examples`.** The text guard honours a repo pattern that names `.env.sample`, so behind the allow it would leak. Omit the rule when the list is empty. Translate as `secret_files._matches` reads them: no `/` becomes `**/<pattern>`. With `/`, strip `./` and write `${PROJECT_ROOT}/<pattern>`, turning each lone `*` into `**`, because `fnmatch`'s `*` crosses `/`.
- `deny_exec`: `command_rules` placed **before** `sssf-allow-rest`, or they never fire.
- `network_allow`: rule `sssf-repo-hosts` on port 443, after `sssf-registries`. `sssf-no-agentsh-api` stays first.

**Reading once.** `read(cfg) -> LocalGuard`, called only from `session.ensure`:

- Load settings from `worktree.main_checkout()` and read `git rev-parse --path-format=absolute --git-common-dir` from the launch cwd. Build one `Policy` per harness in the roster.
- `mode="text"` returns with nothing else read beyond the settings.
- A settings `ValidationError` becomes `SystemExit` with a clear message, as a config error.

**Checks.** `refusal`s raise `AgentshRefused(sandbox.SandboxFailed)`, naming the fixing sudo step. `SandboxFailed` is already in `session.py`'s plain stops and isn't a `RuleBreach`. Every subprocess has a 10 s timeout, and `OSError` or a timeout is a refusal.

`check_machine(guard)`, in order:

1. `systemctl is-active agentsh` prints `active`.
2. `BINARY`'s sha256 equals the pin.
3. `CONFIG` parses, `policies.detect_project_root is False`, and each policy name is in `policies.allowed`.
4. Each installed policy's bytes equal the carried `text`.

`check_root(root)`: `git -C <root> rev-parse --show-toplevel`, resolved, equals `root` resolved.

**Launch.** `start(tree, harness, cmd, *, cwd, env, guard, tally)`:

- `tree is not sandbox.LOCAL`: return `tree.start(cmd, cwd=cwd, env=env)`. A VM run doesn't change.
- `guard is None`: raise `AgentshRefused`.
- `guard.mode == "text"`: return `tree.start(cmd, cwd=cwd, env=env)`, unchanged.
- Otherwise run `check_machine`, and refuse if `Path(cwd).resolve() != guard.root` or `harness` has no carried policy. Then Popen `[BINARY, "wrap", "--policy", name, "--root", guard.root, "--report=false", "--", *cmd]` with stdin `DEVNULL`, the five env vars forced over the caller's env (`os.environ` when `env` is `None`), and return an `AgentshProcess`.
- Don't add a new `Sandbox` class. `agent_cc` and `agent_pi` test `tree is sandbox.LOCAL`, and a second local class would send them down the VM path and fetch a VM token.

**`AgentshProcess`** has the `sandbox.Process` shape, with `pid` being the wrap's. `kill.identify` needs no change: the agent's name is still a token in the wrap's arguments. A test pins that.

- **Output.** A drain thread reads stderr so the pipe can't block. `agentsh:` lines go to the guard, and other lines stay for `.stderr.read()`, which joins the thread with a 5 s limit so it can't hang. `.stdout` is a generator dropping `agentsh:` lines, so they reach neither the raw log nor the parsers.
- **Attach.** The strings come from `internal/cli/wrap.go` in the fork, all on stderr. `start` blocks until it has seen `agentsh: session <id> created (policy: <name>)` with the right name **and** `agentsh: agent ... started with ptrace interception`. The first match of each wins. These end the run with `AgentshRefused`, after `stop_tree`:
  - `running without interception`
  - `agent ... started (pid:` with no mechanism
  - any mechanism other than `ptrace`
  - a wrong policy name
  - the wrap exiting before attach
  - `ATTACH_SECONDS` passing
- **Denial watch.** A thread polls `GET {API}/api/v1/events/search?session_id=<id>&order=asc&limit=5000&offset=<n>` every `POLL_SECONDS`, through a no-proxy opener with a 5 s timeout. It reads pages until one has fewer than 5000 rows, and never asks for more than 5000.
  - `order=asc` is required: the handler defaults to newest first (`internal/api/app.go:1492`, `internal/store/sqlite/sqlite.go:517`, `ORDER BY ts_unix_ns`), where a stored offset drifts on a live session.
  - Writes go through an async queue, so an event can land behind the offset. Each poll restarts at `max(0, seen - OVERLAP)`, and the final read restarts at 0.
  - Dedupe by event `id`, or by the event's canonical JSON if it has none.
  - Accept a list or `{events: [...]}`. Decision is `policy.effective_decision` or `fields.decision`. Rule is `policy.rule` or `fields.rule`.
  - A new counted event calls `total = tally.add()`. At `rules.MAX_REFUSALS`, set `tripped` and `stop_tree`.
  - Three failed polls in a row set a service failure and `stop_tree`.
  - Other denials are collected with their paths.
- **`stop_tree()`.** Under a lock, safe to call twice and from any thread: `rules.stop_tree(pid)`, then `_end_session()`. `destroySession` kills no processes, so the tree goes first.
- **`_end_session()`.** A final read of events, then `DELETE {API}/api/v1/sessions/<id>` with a 5 s timeout, where a 404 is fine. Once only. Failure doesn't raise, and goes in the trace record.
- **`atexit`.** `start` registers `stop_tree` and `close` unregisters it. That covers SIGTERM on pi, whose `run` has no `except BaseException`: `_finalize_when_killed` raises `SystemExit` and the atexit call ends the wrap and session. No text path changes.

Two module-level functions do nothing for any other process type:

- `close(process, on_event)`: never raises. Waits are the caller's. Calls `_end_session()`, emits `{"type": "agentsh_denials", "denials": [...]}` aggregated by type, path and rule with counts, and swallows emit errors, since the agent is already dead.
- `check_stopped(process)`: raises `rules.RuleBreach(agent, AgentRule(rule=f"agentsh rule {name}"), name, command, refusals=total)` if tripped or `total >= MAX_REFUSALS` and at least one exec denial was counted. Raises `AgentshRefused` on a service failure.

### `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

Add a small thread-safe `RefusalTally` with `add() -> int` and `total`. Nothing else in this file changes.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- Replace `tree.start(...)` with `agentsh_guard.start(tree, HARNESS, cmd, cwd=..., env=..., guard=request.local_guard, tally=tally)`.
- In the existing `finally`, after `process.wait()`, call `agentsh_guard.close(process, on_event)`.
- Before the `dog.hung` check, call `check_stopped` and catch `RuleBreach` to set `partial`. With a `final` event, use its reported cost: `_usage(final usage, max(0, cumulative - prior_cost))`, and write the ledger so a resume isn't billed twice. Without one, use `_partial(turns, output)`.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- The same `start` replacement.
- Call `close` in the `on_event` failure branch after `process.wait()`, and on the normal path after `process.wait()`.
- Before `dog.hung`, call `check_stopped`, catching `RuleBreach` to set `stop.partial = result`.

The text checks stay on in both modules.

### `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- In `send()`, make one `rules.RefusalTally`. Pass it to `_event_forwarder` and to the interface through a new `AgentRequest` field, `refusal_tally`, excluded from serialisation, or as a plain keyword if the model can't hold it. With no tally given, the forwarder makes its own, so its callers and text behaviour don't change.
- In the forwarder, `refusals += 1` becomes `refusals = tally.add()`, on the same non-builtin branch.
- Set `local_guard=getattr(run, "local_guard", None)` on the request.
- In `forward`, handle `agentsh_denials` before the rule check by writing a `log` trace event named `agentsh_denials`, then return. It must not count.
- `AgentshRefused` needs no new branch, because it is a `SandboxFailed`. Confirm it fails the phase plainly.

### `.claude/skills/sssf/templates/adws/adw_modules/session.py`

In `ensure`, after `choose_sandbox` and the `sandbox.prepare` block, and **before** `worktree.launching()` and `worktree.enter`:

1. `guard = agentsh_guard.read(cfg)`.
2. If `guard.mode == "agentsh"`, no `sandbox_name` is set, and this isn't a VM resume (`worktree.recorded` from the db path `choose_sandbox` builds, `.get("sandbox")`), call `check_machine(guard)`. The refusal comes before a worktree or a run row exists. Getting the VM resume test wrong can only refuse: the wrap itself depends on `tree is sandbox.LOCAL` alone.

After `_start`:

3. Set `run.local_guard`, with `root` from `run.repo_root`, then call `check_root` under `agentsh`, before `_open_sandbox`, `worktree.set_up` or any agent. Skip `check_root` for a VM run. Import inside the function if `worktree` causes a cycle.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml` (new)

A copy of `docs/designs/agentsh/sssf.yaml`, with `policies.detect_project_root: false`, a `default` and `allowed` the command fills in, and no proof policies. It ships in the skill because `sssf-update` copies nothing else.

### `.claude/skills/sssf/templates/adws/adw_agentsh_policy.py` (new)

Prints each policy and the sudo lines, and never runs sudo. It writes the policies and a merged config under the data dir, in `agentsh/`, which is outside `/tmp` and not tracked. The merged `allowed` is the union of the installed `CONFIG`'s list and this repo's names, so another repo's entry survives. It prints `sudo install -o root -g root -m 0644 <file> <dest>` per file and `sudo systemctl restart agentsh`. Check whether a test lists the stamped files or needs a registry entry, and add it if so.

### Docs

- `.claude/skills/sssf/references/config.md`: `local_guard`, `deny_exec` and `network_allow` in the repo settings table, plus an install step section.
- `.claude/skills/sssf/templates/repo.yaml`: commented examples only.
- `docs/designs/agentsh-local-sandbox.md`: what got built, and what the first real run must check, including the paths under "For the engineer". Update its Status line and Next list.
- There is no CHANGELOG. No `app_docs/` edit is needed.

## Tests

New file `.claude/skills/sssf/tests/test_agentsh_guard.py`, using a fake `agentsh` script and a fake HTTP server, with config built from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`. A test needing the real service skips unless `AGENTSH_LIVE` is set.

Update the `AgentRequest(` helpers in `.claude/skills/sssf/tests/test_agent_cc.py`, `.claude/skills/sssf/tests/test_agent_pi.py`, `.claude/skills/sssf/tests/test_rules.py`, `.claude/skills/sssf/tests/test_secret_files.py` and `.claude/skills/sssf/tests/test_silence.py` to pass `local_guard=LocalGuard(mode="text")`, and any test that runs a real interface through a `Run`. Their assertions don't change.

**Settings and text**

- `text`: command and env identical to `tree.start`, for both harnesses.
- Config error for `agentsh` with `sandbox: vercel`.
- `guard=None` on a local tree refuses, on both harnesses. A VM tree isn't wrapped.

**Read once**

- With a guard carried as `agentsh`, rewriting `repo.yaml` to `text` and repointing the worktree's `.git` file before `start` still gives the wrap with the carried policy.
- `start` makes no git call and doesn't read `repo.yaml`: spy on `subprocess` and `repo_settings.load`.
- `ensure` reads the guard before `worktree.enter`, and every chain's run carries it.

**Refusals**

- Each refusal fires before any agent starts, without a hang and not as `RuleBreach`.
- A `cwd` that isn't `guard.root` refuses.
- Each harness gets its own policy, all five env vars, and stdin `/dev/null`.

**Generated policies**

- The last file rule denies `/` and `/**`.
- Secret and credential rules come before `sssf-workspace`.
- `sssf-git-config` comes before `sssf-git-data`.
- `sssf-repo-secret-files` comes before `sssf-env-examples`.
- `deny_exec` comes before `sssf-allow-rest`.
- A `'` in a value stays one quoted scalar.
- With no settings, file rules equal the `file_rules:` section of `docs/designs/agentsh/deny-policy.py` output, run as a subprocess. Skip only if that file is absent, as in an installed repo.

**Denials**

- Across a full page of 5000 plus a second page, a named `ptrace_execve` denial counts, while file denials and unnamed denials don't.
- Every request has `order=asc` and `limit` at most 5000. The fake server returns 200 rows if `limit` is larger and newest first without `order=asc`.
- Events arriving between polls, one landing behind the offset, and a repeated page each count once.
- Two denials leave the agent running. The third stops it with `refusals=3` and `partial` set, on both harnesses.
- One text refusal plus two exec denials also stops it.
- A third denial seen only in the final read still raises.
- Three failed polls end the run as a failure, not a breach.

**Attach**

- A wrap that exits at once, one saying `running without interception`, one printing `started (pid:` with no mechanism, one naming `seccomp`, one naming another policy, and one that never prints each end as failures.

**Output and stopping**

- `agentsh:` lines don't reach `on_event` or the raw log.
- `stop_tree` kills a child under the fake wrap and sends the session DELETE once, even if called twice.
- A SIGTERM to a process running a pi call under the fake wrap leaves no child and sends the DELETE.
- The denial trace event carries paths and adds nothing to the tally.
- `kill.identify` accepts the wrap's command line for both harnesses.

Add to `.claude/skills/sssf/tests/test_secret_files.py` a check that `fnmatch` and generated forms agree for the sample patterns.

## Verification

- The repo's own test command passes, which runs `pytest tests` in `.claude/skills/sssf`, on a machine without the service. It collects the new file, so no extra check is needed.
- The existing `agent_cc`, `agent_pi` and `rules` tests change only in the helper lines above.
- The live checks under `AGENTSH_LIVE` aren't run in this change. Rollout step 2 runs them.

## For the engineer

These are protected, so the builder must not touch them:

1. `adws/adw_guard_change.py`: add `agentsh_guard.py` to `GUARD_MODULES`.
2. `CLAUDE.md`: add `agentsh_guard.py` to the guard code list.
3. `adws/tests/test_guard_change.py`: add a matching assertion if you want one pinned.

**A gap to decide before rollout step 2.** The request says that with no settings the file rules must match `deny-policy.py`, so this plan adds no other allow. But on a `--worktree` run, these paths sit in the main checkout, outside `${PROJECT_ROOT}`:

- `adws/adw_data/sessions/<id>/` holds `context_handoff` and pi's `--session-dir`.
- `REFUSE_EXTENSION` and `REFUSE_SHELL` resolve from `__file__`, so they may sit there too.

Default-deny will refuse them, so a worktree chain will likely fail closed. It is not a bypass. The likely fix is an allow on the sessions directory, one more rule in the policy and one more sudo step, which widens what an agent can read across runs. That is your call. The design doc records it as the first real run's first check.
