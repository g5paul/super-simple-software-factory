# Keep the obs token out of every VM command

## Summary

`sandbox_remote.Remote._env` builds the environment for every VM command, both
`run` (checks, git, `worktree_setup`) and `start` (Claude Code and pi agents). It
passes every host variable whose name starts with `SSSF_` or `OBS_`. So
`OBS_AUTH_TOKEN`, which the ADW loads from the repo's `.env` through
`utils.load_dotenv()`, reaches every VM command except pi agents. Run a761dcc0
already strips it there, in `agent_pi._in_vm`. A check or test script an agent
writes can copy the token into the clone, and the next commit's bundle carries
it back to the host branch.

The fix: `Remote._env` drops `OBS_AUTH_TOKEN` by exact name, after the prefix
filter, for every command and whatever `env` the caller passes. Every other
`SSSF_` and `OBS_` variable still crosses. The host tree (`sandbox.Local`) never
calls `_env`, so a host run is unchanged.

## Threat model

- The agent runs as the engineer's own user. On the host it can read `.env`
  and the process environment directly. This guard can't stop that, and isn't
  meant to. Its job is narrower: a VM run must not hand the obs token to code
  in the VM.
- In the VM, the agent controls the code that runs there. It can write a test,
  a check script or a `worktree_setup` step that a quality check later runs
  through `Remote.run`, and it can read its own process environment when it
  runs through `Remote.start`. Whatever `_env` returns, that code can read, and
  it can write it into the clone, where `commits_out` brings it back to the host
  branch.
- At this level, the guard decides which host variables enter the VM. It can't
  stop a secret the repo committed, or one the operator puts in some other
  variable that does cross (for example an `SSSF_` or `OBS_` name of their own
  choosing that holds the same token).

## Baseline

Branch `guard-vm-extensions` at commit `af13527` ("Let pi extensions run in a VM
when committed at the run's starting commit"). This branch,
`guard-vm-obs-token`, starts there too. Judge every finding against `af13527`.

At the baseline:
- `PASSED = ("SSSF_", "OBS_")` at
  `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py:42`.
- `Remote._env` (`sandbox_remote.py:472`) keeps every variable with those
  prefixes, then adds `CLAUDE_CODE_SHELL_PREFIX` when refuse rules are on, and
  renames `SSSF_PI_TOKEN` to `OPENAI_API_KEY`.
- `agent_pi._in_vm` (`agent_pi.py:276`) pops `OBS_AUTH_TOKEN` and sets
  `OBS_DISABLE=1` for pi agents only.

## Bar

The change must:
- Make sure no VM command, whether `run` or `start`, gets `OBS_AUTH_TOKEN`,
  whether the variable comes from `os.environ` (`env=None`) or from an `env`
  dict the caller passes.
- Keep passing every other `SSSF_` and `OBS_` variable exactly as the baseline
  does (`OBS_NAME`, `OBS_POOL`, `OBS_TAG`, `OBS_DISABLE`, `SSSF_ADW_ID`,
  `SSSF_REFUSE_RULES`, and so on), and keep the `CLAUDE_CODE_SHELL_PREFIX`,
  `SSSF_PI_TOKEN` → `OPENAI_API_KEY` and `UV_PYTHON` handling as it is.
- Leave a host run exactly as it is. `sandbox.Local.run`/`start` pass `env`
  through untouched and must stay that way, so host checks and host agents
  still see `OBS_AUTH_TOKEN` when the host has it.

The change must never, where the baseline does otherwise:
- keep an agent file, run agent code, or delete or overwrite an engineer file.
  This change only drops one name from a dict, so it touches no files at
  runtime.
- pass a host variable into the VM that the baseline keeps out.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

- Below `PASSED`, add a constant for the names that never cross, with a
  comment pointing at design decision 23:

  ```python
  HELD = ("OBS_AUTH_TOKEN",)          # never crosses, whatever the prefix (design decision 23)
  ```

- In `Remote._env`, change the filter so it drops held names:

  ```python
  passed = {k: v for k, v in source.items() if k.startswith(PASSED) and k not in HELD}
  ```

  Leave the rest of `_env` as it is. `run` and `start` are its only callers,
  and every other `sandbox.run_process` call in the module (`_must`, `_test`,
  `size`, `read_text`, `_cpu_now`, `open`, and so on) passes no `env`, so this
  one line covers every VM command.

- Update the module docstring's `run` bullet (line 8). It currently reads
  "It gets SSSF's own variables, never the Claude token." Make it say it gets
  SSSF's own and obs variables, never the Claude token or the obs server's
  token. Then add to the `start` bullet that the obs token stays on the host
  here as well. Keep the wording short, in the file's style.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

No code change. Keep `env.pop("OBS_AUTH_TOKEN", None)` in `_in_vm`. It is now
redundant, but harmless, and the existing test
`test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled` covers it.
Don't edit the `_in_vm` docstring: it still holds.

### 3. `.claude/skills/sssf/tests/test_sandbox_remote.py`: new tests

Add these after `test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled`,
at the end of the file. Each name must contain `obs_token`, because the plan's
check selects them with `-k obs_token`. They need no SSSF config, since
`Remote._env` reads none. If a later edit makes one need config, build it from
`.claude/skills/sssf/templates/sssf.config.yaml`, never a test-only one.

a. `test_no_vm_command_gets_the_obs_token_and_other_obs_variables_cross(box, monkeypatch)`.
   This runs on both providers through the `box` fixture.
   - `monkeypatch.setenv("OBS_AUTH_TOKEN", "obs-secret")`,
     `monkeypatch.setenv("OBS_NAME", "builder")`,
     `monkeypatch.setenv("SSSF_EXTRA", "1")`.
   - `box.run(["sh", "-c", "env"])`, with the default env: stdout has no
     `OBS_AUTH_TOKEN` and no `obs-secret`, and it has `OBS_NAME=builder`,
     `SSSF_EXTRA=1` and `SSSF_ADW_ID=abc`.
   - `box.run(["sh", "-c", "env"], env={**os.environ})`, with an explicit env:
     the same assertions.
   - `box.run("env", shell=True)`: the same assertions. This is the path
     `quality.py` checks take.
   - `box.start(["sh", "-c", "env"])` and
     `box.start(["sh", "-c", "env"], env={**os.environ, "OBS_AUTH_TOKEN": "obs-secret"})`:
     join `process.stdout`, then `process.wait()`. The output has no
     `OBS_AUTH_TOKEN` and no `obs-secret`, and it has `OBS_NAME=builder` and
     `CLAUDE_CODE_OAUTH_TOKEN`.
   - Also assert on the fake's own record, so the test doesn't rely on the
     shell's output alone: for every entry in `box.raw.calls` that has an `env`
     key, `"OBS_AUTH_TOKEN" not in entry["env"]`. Both fakes record the env
     passed to each call under `env`: Vercel's at `test_sandbox_remote.py:60`,
     E2B's at `test_sandbox_remote.py:156`.

b. `test_a_host_command_still_gets_the_obs_token(tmp_path)`. This one has no
   box.
   - `from adw_modules import sandbox`
   - `done = sandbox.LOCAL.run(["sh", "-c", "env"], env={**os.environ, "OBS_AUTH_TOKEN": "obs-secret"}, cwd=tmp_path)`
     (`Local.run` returns text by default; see `sandbox.py:79`.) Assert
     `"OBS_AUTH_TOKEN=obs-secret" in done.stdout`.
   - Do the same with `sandbox.LOCAL.start([...], env=..., cwd=tmp_path)`. Read
     its stdout lines, then `wait()`.

Don't change any existing test.

### 4. `.claude/skills/sssf/references/config.md`, line 424

Replace the paragraph
"In the VM, pi agents get `OBS_DISABLE=1` and no `OBS_AUTH_TOKEN`, so an obs
extension loads but posts nothing. SSSF's trace still records the run. Other
`OBS_*` variables cross."
with text that says:
- The obs server's token, `OBS_AUTH_TOKEN`, never enters a VM. No check, git
  command, setup step or agent of either harness gets it there. A host run
  still passes it as before.
- pi agents in the VM also get `OBS_DISABLE=1`, so an obs extension loads but
  posts nothing. SSSF's trace still records the run.
- Every other `OBS_*` variable crosses, and so does every `SSSF_*` variable.

Keep it to two or three sentences in the file's plain style.

### 5. `docs/designs/remote-sandboxes.md`, "As built: pi extensions" (line 169)

The bullet "`OBS_AUTH_TOKEN` is held back for pi agents only." is now stale.
Replace it with a bullet saying the token was first held back for pi agents
only, and that `sandbox_remote.Remote._env` now holds it back from every VM
command. Don't touch decision 23 or the "Open" relay bullet. Both stay true.

### Docs checked and left alone

- `README.md`, `SKILL.md` and the other `references/*.md` don't mention
  `OBS_AUTH_TOKEN` (grep shows only `config.md:424`).
- The repo keeps no CHANGELOG.
- `app_docs/` is for the documenter. If a write-up there says that `run`
  commands or Claude Code agents in a VM get all `OBS_*` variables, or that the
  token is held back for pi agents only, the documenter should note that this
  change supersedes it.

## Verification

1. New tests (the plan's `checks` entry), from the repo root:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_remote.py -q -p no:cacheprovider -k obs_token`
   All selected tests pass, on both the `vercel` and `e2b` params. Against the
   baseline `sandbox_remote.py`, test (a) must fail. Confirm this by temporarily
   reverting the `_env` line, then restore it. Test (b) passes on both.
2. Full file:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_remote.py tests/test_agent_pi.py -q -p no:cacheprovider`
   Known baseline noise: when the shell running pytest has
   `SSSF_REFUSE_RULES` and `CLAUDE_CODE_SHELL_PREFIX` set, as it does inside an
   SSSF agent, `test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules`
   fails on both params at `af13527` too, because it copies `os.environ`. It
   fails the same way before and after this change. Any other failure counts.
   To see the whole file green, run it with
   `env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX` in front.
3. The repo's own quality checks pass.
4. `grep -n "OBS_AUTH_TOKEN" .claude/skills/sssf/references/config.md` shows
   the new wording, which says the token never enters a VM.

No new tool or dependency is needed. The check uses `uv` and the same
`--with` packages the test file's docstring names.

## Out of scope

These holes stay open after this change. File them as old holes:

- **Other secrets under crossing prefixes.** Any other secret the operator
  keeps under an `SSSF_` or `OBS_` name still crosses. This includes the same
  obs token copied into a different `OBS_` name. Only the exact name
  `OBS_AUTH_TOKEN` is held.
- **Tokens that cross on purpose.** `CLAUDE_CODE_OAUTH_TOKEN` (Claude Code
  agents) and the ChatGPT bearer token (`SSSF_PI_TOKEN` → `OPENAI_API_KEY`, pi
  agents) still enter the VM by design. An agent can copy them into the clone
  the same way.
- **Secrets the repo commits.** A committed `.env`, or any committed file that
  holds a secret, reaches the clone whatever `_env` does.
- **Host runs.** On the host, agents and checks run as the engineer and can
  read `.env` and the environment directly.
- **Content coming back.** Nothing scans the bundles or the handoff folder
  that return from the VM for secrets.
- **Obs events from a VM.** Still off (decision 23). The relay remains under
  Open in `docs/designs/remote-sandboxes.md`.

## For the engineer

- `adws/adw_modules/sandbox_remote.py` is this repo's installed copy of the
  module, and it is protected. The builder changes only the template under
  `.claude/skills/sssf/templates/`. This repo's own runs use the old `_env`
  until you refresh the installed copy, for example with
  `install.py --update`.
- Decision 27's VM proof, item (5), checks `OBS_AUTH_TOKEN` for a pi agent
  only. When you next run the proof in sssf-sandbox, also check that a check
  command run through `Remote.run` and a Claude Code agent in the VM don't see
  it.
