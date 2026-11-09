# Run local agents under agentsh when a repo asks for it

Rollout step 1 of `docs/designs/agentsh-local-sandbox.md`. Its Decisions table is settled.

## Threat model

The agent runs as the engineer's user. It can run anything, spell it any way, and edit its workspace. agentsh checks real execs, opens and connects, and the agent can't reach the root-owned config or the service. This guard can't stop a copied binary running under a new name, or harm done by quality checks and git, which run outside the wrap. An agent can edit the repo.yaml in its own worktree, so **the guard reads settings from `worktree.main_checkout()`, never the tree.** That checkout is outside the workspace and default-deny covers it.

## Baseline

`main` at `b208789`: the text guard alone. Every finding is judged against it.

## Bar

- `local_guard: text` or unset: every command, env var and refusal is byte for byte as baseline.
- `agentsh`: no agent process runs outside the wrap or under a policy other than the generated one, and nothing the text guard stops gets through because of this change. No state lets a run go on after the service or the attach has failed.
- No generated policy lets an agent read or change what `docs/designs/agentsh/deny-probe.sh` shows held.
- A wrong setting, stale policy or missing service stops the run with a message naming the sudo step. It never hangs and is never a breach.
- No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise.

## Out of scope

Accepted, not findings: a copied binary, an agent reading its own sign-in, Claude Code writing `~/.claude.json` and reading other projects' transcripts, shared `/tmp`, the server API open to unwrapped local processes, quality checks outside the wrap. Also out: agentsh's own bugs, the pi hang, turning this on anywhere, patching or removing text checkers, `--sandbox` providers, sudo, and running a real agent. `claude auth status`, `pi --list-models` and `pi auth print-bearer-token` are SSSF's own control calls, take no prompt, and stay outside the wrap.

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

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` (new)

Constants, which tests monkeypatch: `BINARY=/usr/bin/agentsh`, `CONFIG=/etc/agentsh/sssf.yaml`, `POLICY_DIR=/etc/agentsh/policies`, `SERVER=unix:///run/agentsh/agentsh.sock`, `API=http://127.0.0.1:18080`, `PINNED_SHA256=3774b1170d447e8c935fb8c2d7421199b6d3fafc312d6f77de1bb2cd1c5db45d` (build `41960715`), `ATTACH_SECONDS=30`, `PAGE=5000`.

**Generation.** `policy_name(main, harness)` returns `sssf-<slug of main.name>-{claude|pi}`. `policy_text(settings, git_common_dir, harness)` is a port of `deny-policy.py` and `sssf-net.yaml`, with their constants, comments, rule order and reasons kept verbatim, since `docs/` doesn't ship. It uses the same `rule()` format so output stays byte-comparable, but doubles any `'` in a path so a value can't break out of its YAML quote. Additions:

- Git common dir: absolute, from `git rev-parse --path-format=absolute --git-common-dir`. The same in every worktree, so the policy is per repo.
- `secret_files`: **a rule `sssf-repo-secret-files` goes first, before `sssf-env-examples`.** The text guard honours a repo pattern that names `.env.sample`, so behind the allow it would leak. Omit the rule when the list is empty. Translate as `_matches` reads them: no `/` becomes `**/<pattern>`. With `/`, strip `./` and write `${PROJECT_ROOT}/<pattern>`, turning each lone `*` into `**`, because `fnmatch`'s `*` crosses `/`.
- `deny_exec`: `command_rules` placed **before** `sssf-allow-rest`, or they never fire.
- `network_allow`: rule `sssf-repo-hosts` on port 443, after `sssf-registries`. `sssf-no-agentsh-api` stays first.

**Preflight.** `check(harnesses, cwd)` raises `AgentshRefused(sandbox.SandboxFailed)` with the fixing sudo step. Every subprocess has a 10 s timeout, and `OSError` or a timeout is a refusal. In order:

1. `systemctl is-active agentsh` prints `active`.
2. `BINARY`'s sha256 equals the pin.
3. `CONFIG` parses, `policies.detect_project_root is False`, and each policy name is in `policies.allowed`.
4. Each installed policy's bytes equal `policy_text` for today's settings.
5. `git rev-parse --show-toplevel` from `cwd`, resolved, equals `cwd` resolved.

**Launch.** `start(tree, harness, cmd, *, cwd, env)`:

- If `tree is not sandbox.LOCAL`, or settings from `worktree.main_checkout()` say `text`, return `tree.start(cmd, cwd=cwd, env=env)` unchanged.
- Otherwise run `check`, then Popen `[BINARY, "wrap", "--policy", name, "--root", top, "--report=false", "--", *cmd]` with stdin `DEVNULL`, the five env vars forced over the caller's env, and return an `AgentshProcess`.
- Don't add a new `Sandbox` class. `agent_cc` and `agent_pi` test `tree is sandbox.LOCAL`, and a second local class would send them down the VM path and fetch a VM token.

**`AgentshProcess`** has the `sandbox.Process` shape, with `pid` being the wrap's:

- A drain thread reads stderr, so the pipe can't block. `agentsh:` lines go to the guard. Other lines stay for `.stderr.read()`.
- `.stdout` is a generator dropping `agentsh:` lines, so they reach neither the raw log nor the parsers.
- Attach (`internal/cli/wrap.go` in the fork, all on stderr): `start` blocks until it has seen `agentsh: session <id> created (policy: <name>)` with the right name **and** `agentsh: agent ... started with <mechanism> interception`. First match wins, so an agent can't forge a later one. These all end the run with `AgentshRefused`, after `stop_tree`:
  - a line containing `running without interception`
  - a line `agent ... started (pid:` with no mechanism
  - a wrong policy name
  - the wrap exiting before attach
  - `ATTACH_SECONDS` passing
- Denial watch: a thread polls `GET {API}/api/v1/events/search?session_id=<id>&limit=5000&offset=<n>` every 5 s through a no-proxy opener with a 5 s timeout. It reads a page until it gets fewer than 5000. Never ask for more than 5000.
  - Accept a list or `{events: [...]}`.
  - Decision is `policy.effective_decision` or `fields.decision`. Rule is `policy.rule` or `fields.rule`.
  - Count events only where type is `ptrace_execve`, decision is `deny` and the rule is non-empty. At `rules.MAX_REFUSALS`, set `tripped` and `stop_tree`.
  - Three failed polls in a row stop the tree and set a service failure.
  - Every other denial is collected with its path.
- `stop_tree()`: `rules.stop_tree(pid)`, then `DELETE {API}/api/v1/sessions/<id>` with a 5 s timeout. Safe to call twice.
- `finish(on_event)`, called after the wait, on the main thread:
  - Do one final page drain, and don't fail if it can't complete.
  - Emit `{"type": "agentsh_denials", "denials": [...]}` aggregated by type, path and rule with counts.
  - End the session.
  - Raise `rules.RuleBreach(agent, AgentRule(rule=...), rule_name, command, refusals=n)` if tripped. That is the existing three-refusal stop, which reads "Nothing refused ran". Raise `AgentshRefused` on a service failure.

`after_exit(process, on_event)` calls `finish` on an `AgentshProcess` and does nothing otherwise.

Exec-denial and text-refusal tallies stay separate: a text-refused call never reaches exec.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`, `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Replace `tree.start(...)` with `agentsh_guard.start(tree, HARNESS, cmd, cwd=..., env=...)`. Call `agentsh_guard.after_exit(process, on_event)` right after `process.wait()` on the normal path, before the `dog.hung` check. Nothing else changes, and the text checks stay on.

### `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

In `_event_forwarder`'s `check`, handle `agentsh_denials` first by writing a `log` trace event named `agentsh_denials`, then return. It must not count or reach the rule check. Add `AgentshRefused` handling only if it isn't already an ordinary failed stop.

### `.claude/skills/sssf/templates/adws/adw_modules/session.py`

In `ensure`, when no VM is chosen, call `agentsh_guard.check` for the roster's harnesses before the worktree or any agent, from the main checkout's settings. The `--root` step runs in `start`, once the tree exists. A `--sandbox` run, new or resumed in a VM, skips it. Watch for import cycles with `worktree`: import inside the function if needed.

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

- `text`: command and env identical to `tree.start`.
- Config error for `agentsh` with `sandbox: vercel`.
- Each refusal fires before any agent starts, without a hang and not as `RuleBreach`.
- Each harness gets its own policy, all five env vars, and stdin `/dev/null`.
- A VM tree isn't wrapped.
- Generated policies:
  - The last file rule denies `/` and `/**`.
  - Secret and credential rules come before `sssf-workspace`.
  - `sssf-git-config` comes before `sssf-git-data`.
  - `sssf-repo-secret-files` comes before `sssf-env-examples`.
  - `deny_exec` comes before `sssf-allow-rest`.
  - A `'` in a value stays one quoted scalar.
- With no settings, file rules equal the `file_rules:` section of `docs/designs/agentsh/deny-policy.py` output, run as a subprocess. Skip only if that file is absent, as in an installed repo.
- Denials: across a page of 5000 plus a second page, a named `ptrace_execve` denial counts, while file denials and unnamed denials don't. Three stop the agent with `refusals=3`. No request has `limit` over 5000.
- Attach: a wrap that exits at once, one saying `running without interception`, one printing `started (pid:` with no mechanism, and one that never prints all end as failures.
- `agentsh:` lines don't reach `on_event` or the raw log.
- `stop_tree` kills a child under the fake wrap and sends the session DELETE.

Add to `.claude/skills/sssf/tests/test_secret_files.py` a check that `fnmatch` and generated forms agree for the sample patterns.

## Verification

- `uv run pytest .claude/skills/sssf/tests/test_agentsh_guard.py -q` passes on a machine without the service.
- The repo's own test command passes, and the existing `agent_cc`, `agent_pi` and `rules` tests are unchanged.

## For the engineer

These are protected, so the builder must not touch them:

1. `adws/adw_guard_change.py`: add `agentsh_guard.py` to `GUARD_MODULES`.
2. `CLAUDE.md`: add `agentsh_guard.py` to the guard code list.
3. `adws/tests/test_guard_change.py`: add a matching assertion if you want one pinned.

**A gap to decide before rollout step 2.** The request says that with no settings the file rules must match `deny-policy.py`, so this plan adds no other allow. But on a `--worktree` run, these paths sit in the main checkout, outside `${PROJECT_ROOT}`:

- `adws/adw_data/sessions/<id>/` holds `context_handoff` and pi's `--session-dir`.
- `REFUSE_EXTENSION` and `REFUSE_SHELL` resolve from `__file__`, so they may sit there too.

Default-deny will refuse them, so a worktree chain will likely fail closed. It is not a bypass. The likely fix is an allow on the sessions directory, one more rule in the policy and one more sudo step, which widens what an agent can read across runs. That is your call. The design doc records it as the first real run's first check.
