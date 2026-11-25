# Keep a project's pi files out of the pi SSSF starts on the host

## Goal

Every `pi` that SSSF starts on the host, outside any wrap, starts in an empty folder of SSSF's own and reads, loads and runs nothing from the project. The model list also gets `--no-approve`. Agent calls keep their `cwd` and flags.

## Baseline

`main` at `43b866d`. Every finding is judged against that commit.

pi's source was read from `<npm global>/@earendil-works/pi-coding-agent/dist`, version 1.4.0, on 2026-11-24. No pi was run while planning. Paths below that start `main.js`, `core/` or `cli/` are in that folder.

## What each host start reads from `cwd` at the baseline

No host start passes `cwd=`, so each runs in the orchestrator's working folder: the project's checkout or a run's worktree.

| # | Start | Where | What pi reads from `cwd` today |
|---|---|---|---|
| 1 | `pi --list-models` + `HOST_FLAGS` | `agent_pi._pi_catalog`, `agent_pi.py:92`, env `operator_env()` (`.env` keys kept) | See the list below. |
| 2 | `pi auth print-bearer-token --provider <p> --min-expiry 35m` | `agent_pi._host_token`, `agent_pi.py:382`. Callers: `vm_token` (`agent_pi.py:397`, from `_in_vm` at 613 and `sandbox.py:239`), `_sign_in` (`agent_pi.py:634`) | Nothing. |
| 3 | `pi auth check --provider <p> --json` | `agent_pi._host_token(check=True)`, `agent_pi.py:384`, from `_sign_in` (651) | Nothing. |
| 4 | `pi auth check --provider <p> --json` | `trial.pi_problems`, `trial.py:136`, env `agent_pi.child_env()` | Nothing. |
| 5 | `pi auth print-bearer-token --provider openai --min-expiry 35m` | `templates/container/pi_token_broker.py:36`, started on the host by `container/run.sh:39`, inherited env and `cwd` | Nothing. |

What start 1 reads, in pi's order:

- `main.js:467` takes `cwd = process.cwd()`. Line 469 builds a settings manager with `projectTrusted: false` and uses only its global settings.
- `main.js:533` `runMigrations(cwd)` → `migrations.js:279` `migrateExtensionSystem(cwd)` → lines 234-237 `migrateCommandsToPrompts(<cwd>/.pi)`, which **renames** `<cwd>/.pi/commands` to `<cwd>/.pi/prompts` (`migrations.js:124-129`), and lines 239-242 stat `<cwd>/.pi/hooks` and list `<cwd>/.pi/tools`. This runs whatever the trust is and whatever the flags are.
- `main.js:535` `SettingsManager.create(cwd, agentDir)` with no trust option. `projectTrusted` defaults to `true` (`core/settings-manager.js:238`), so it parses `<cwd>/.pi/settings.json` (`settings-manager.js:229`). Its values feed `getSessionDir()` (`main.js:554`). `getDefaultProjectTrust()` reads global settings only (`settings-manager.js:778-781`). For `--list-models` the session manager is in memory (`main.js:286-287`), so `sessionDir` is not used on this start.
- `main.js:581` and `594` `hasTrustRequiringProjectResources(cwd)` (`core/trust-manager.js:151-169`): `existsSync` on `<cwd>/.pi/{settings.json,mcp.json,extensions,skills,prompts,themes,SYSTEM.md,APPEND_SYSTEM.md}` and on `.agents/skills` in `cwd` and every folder above it, except `~/.agents/skills`.
- `main.js:596-601`: with no `--approve`/`--no-approve`, the project is trusted when `<agentDir>/trust.json` says so for `cwd` or a folder above it (`trust-manager.js:21-28`), or global `defaultProjectTrust` is `always` (`core/project-trust.js:38-46`). Trusted, the runtime settings manager loads `<cwd>/.pi/settings.json`, and the resource loader resolves its `packages`, installing a missing one with its `npmCommand` (`core/package-manager.js:1014-1018`). `--no-extensions` does not stop the resolve. That is the command the open row names.
- `--no-context-files` turns off the `AGENTS.md`/`CLAUDE.md` walk up from `cwd` (`core/resource-loader.js:175-194`); `--no-skills` turns off skills. Both are already in `HOST_FLAGS`.

Why starts 2 to 5 read nothing: `cli.js` and `cli/setup.js` read nothing from `cwd`. `main()` calls `runAuthCommand` at `main.js:460` and returns at 461. `runAuthCommand` (`main.js:99`) parses its args, builds a `ModelRuntime` from the agent dir and auth storage, and prints. The first `process.cwd()` in `main.js` is line 467. The other `process.cwd()` uses in `dist` (`utils/photon.js:38`, `utils/paths.js:83`, `core/sdk.js:70`, `core/session-manager.js:1344,1364`, `core/session-export.js:26`, `modes/interactive/bug-report.js:141`, `extensions/mcp/index.js:207`, `package-manager-cli.js:738,827`, `cli/startup-ui.js:49`, `cli/file-processor.js:18`) are not on the auth path.

Not host starts, so not changed:

- `pi --version` in `sandbox_remote.py:114` (run in the VM, line 805), `sandbox_openshell.py:172` and `scripts/e2b_template.py:50` (image build lines). `--version` exits at `main.js:503-506`, before line 533, in any case.
- `agent_pi.run`, `agent_pi.py:836`: the agent call. See "Agent calls".
- `templates/harness_engineering/subagents.ts:229`: started by an agent's pi, inside the wrap. Its own `docs/open.md` row.
- `scripts/install.py:713` prints a command and runs none.

## The fix, and why

**Both: a `cwd` of SSSF's own for every host start, and `--no-approve` for the model list.**

- `--no-approve` alone is not enough. It sets `projectTrustOverride = false` (`cli/args.js:232-233`), so `main.js:596-601` gives `projectTrusted = false` and `settings-manager.js:258` returns `{}` for the project scope, and no project package resolves. It leaves the migration at `main.js:533`, which renames a folder in the project, and the default-trusted startup settings at `main.js:535`. A rename in the project's tree is a write outside SSSF's folders, so the flag alone fails the bar.
- A moved `cwd` alone covers everything pi reads by `cwd` today, but rests on the folder staying empty. With `local_guard: none`, or through a hole, an agent may write there. `--no-approve` is the second layer: if a `.pi/settings.json` turns up in the folder, pi still ignores it.
- `pi auth` gets the `cwd` and no flag. It reads nothing from `cwd` and returns before trust exists, so the flag would do nothing, and the auth argv is pinned by `tests/test_pi_token_broker.py:72`, `tests/test_rules.py:669` and `tests/test_sandbox.py:129`. Moving its `cwd` means the claim no longer rests on one pi version's source.

**The folder: `~/.local/state/sssf-pi-host`.** SSSF makes it, mode `0700`, beside `~/.local/state/sssf-pi` (`agentsh_guard.PI_HOMES`).

- Under agentsh no agent can write it. The pi policy's write allow is `${HOME}/.local/state/sssf-pi/**` (`agentsh_guard.PI_WRITE`), which does not match `sssf-pi-host`; the Claude policy allows nothing under `~/.local/state` but `claude/**`. Everything else falls to `sssf-default-deny`. No policy changes.
- It is fixed, not a new temp folder per call, so nothing piles up and `/tmp`, which agents can write, is not a parent.
- Its parents are `~/.local/state`, `~/.local`, `~`, `/home` and `/`. They are the engineer's, not the project's. pi reads `.pi/` in `cwd` only, never in a parent (`settings-manager.js:229`, `migrations.js:234`, `trust-manager.js:155`), so `~/.pi` is not read as a project. The two walks up are `AGENTS.md`/`CLAUDE.md`, off by `--no-context-files`, and `.agents/skills`, which is only stat-ed and is off by `--no-skills` and `--no-approve`.
- Before each start SSSF checks the folder is a real folder (not a link), owned by this user, and **empty**. If not, it stops with `SandboxFailed` naming the folder. It never deletes what it finds there.
- pi writes nothing in it: the model list's session is in memory (`main.js:286-287`) and `pi auth` never reads `cwd`.

**One seam, `SSSF_PI_HOST_CWD`.** When set, it replaces the default path, and gets the same checks. The tests need it: the suite runs under agentsh, where `~/.local/state/sssf-pi-host` can't be made, and some tests start SSSF's scripts as child processes, which a `monkeypatch.setattr` doesn't reach. It is read from the orchestrator's environment, which no agent sets.

## The model list stays the same

`--list-models` prints `listModels(modelRuntime, ...)` (`main.js:720-723`). The model runtime is built from the agent dir: `core/agent-session-services.js:59` passes `modelsPath: join(agentDir, "models.json")`, and `agentDir` is `getAgentDir()` (`main.js:468`), from `PI_CODING_AGENT_DIR` or `~/.pi/agent`, never from `cwd`. Built-in providers come from pi's own package, and sign-ins from `<agentDir>/auth.json` and the environment, which this change leaves alone. A project adds a provider only through a project extension or package, and `--no-extensions` already drops the first. So for the engineer's own `~/.pi/agent/models.json` and the built-in providers the rows are the same. The one difference is intended: a trusted project's package that registered a provider no longer does.

## Agent calls

A wrapped pi agent keeps `cwd=request.cwd` and `AGENT_FLAGS`, with **no** `--no-approve`.

- What it would lose: project `.pi/skills` and `.agents/skills`, `.pi/prompts`, `.pi/SYSTEM.md` and `APPEND_SYSTEM.md`, and the project's `.pi/settings.json`. `AGENTS.md` and `CLAUDE.md` are not gated by trust, so they would stay.
- Why not: `references/config.md` ("Discovery is off") promises a repo's skills and context files to its agents. A command a project's `settings.json` runs in an agent's pi runs inside the wrap or the VM, with `child_env()` and not `.env`'s keys, where the agent can already run commands with `bash`. That shows no hole past what the agent has. Under agentsh the agent's own agent dir has no `trust.json` from the engineer, so pi already treats the project as untrusted unless an agent wrote one there, which is the open row on shared agent dirs.

## Threat model

- **What the agent can do.** It runs as the engineer's own user. It can get a file into the project's `.pi/` through the ignored-files hole, or in a project that gitignores `.pi/`. At the baseline, the next host `pi --list-models` in a project pi trusts runs that file's `npmCommand` as the engineer, unwrapped, with `.env`'s keys; in any project, trusted or not, it renames `.pi/commands`.
- **What this guard can't stop at its level.** With `local_guard: none` an agent can write `~/.local/state/sssf-pi-host`, `~/.pi/agent/settings.json`, `~/.pi/agent/trust.json` or `PATH`'s `pi` itself; the empty check and `--no-approve` only narrow the first. The empty check and the start are two steps, so a file can land between them; `--no-approve` covers the settings and packages then, and not the migration's rename inside SSSF's own folder. The engineer's own `~/.pi/agent/settings.json` `packages` still resolve in the host's model list, as `references/config.md` already says.

## Bar

Against `43b866d`:

- **Must.** No host start of pi has its `cwd` in the project or a run's worktree. The model list's argv has `--no-approve`. With the planted `.pi/settings.json`, no agent code runs on the host from it and no file in the project is renamed, written or deleted by a host pi.
- **Must.** `_pi_catalog` returns the same rows as the baseline for the same fake `pi` output.
- **Must never.** Change an agent call's `cwd` or flags. Change the auth argv. Delete or overwrite anything found in the host folder. Write under `~/.pi`. Change the agentsh policies.
- **Must.** Every existing test passes on the host, `tests/test_agent_pi.py`'s argv tests among them.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Leave `HOST_FLAGS` as it is; `test_the_host_call_has_discovery_off_and_the_operators_environment` pins its five members. Add beside it `NO_PROJECT = ("--no-approve",)` with a comment: pi ignores project-local files (`cli/args.js:232`), second layer under `host_cwd`.
- Add `HOST_CWD_NAME = ".local/state/sssf-pi-host"` and `HOST_CWD_VAR = "SSSF_PI_HOST_CWD"`, and:

  ```python
  def host_cwd() -> str:
      """The folder every pi SSSF starts on the host runs in: empty, SSSF's own, no project in it."""
  ```

  It takes `os.environ.get(HOST_CWD_VAR)` or `str(Path.home() / HOST_CWD_NAME)`, read per call. It makes the parent with `os.makedirs(..., exist_ok=True)` and the folder with `os.mkdir(folder, 0o700)`, passing `FileExistsError`, the way `_pi_homes` does. Then `os.lstat`: it must be a directory (so not a link), `st_uid == os.getuid()`, and `os.listdir(folder) == []`. Any `OSError` or failed check raises `sandbox.SandboxFailed` with the folder's path, what is wrong, and "Look at it with `ls -la`, move what is there and start again." It deletes nothing.
- `_pi_catalog`: call `cwd = host_cwd()` **before** the `try`, so a `SandboxFailed` goes up like the broker's and nothing is cached. Run `[PI_PATH, "--list-models", *HOST_FLAGS, *NO_PROJECT]` with `cwd=cwd`. Update the docstring: where pi starts and why.
- `_host_token`: add `cwd=host_cwd()` to the one `subprocess.run`. Keep the literal `[PI_PATH, "auth", "print-bearer-token"` and the argv; `tests/test_sandbox.py:129` matches the text. Update the docstring.
- `vm_token`: reword the comment at line 396 to say `pi auth` returns before pi reads `cwd` (`main.js:460`, `467`) and starts in `host_cwd()` all the same.
- `run`: no change. Add one line to its docstring: the agent's pi keeps the repo as `cwd` and gets no `--no-approve`.
- `_sign_in`'s `except (OSError, subprocess.TimeoutExpired, ValueError)` stays as it is; `SandboxFailed` from `host_cwd()` goes up through it.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/trial.py`

In `pi_problems`, pass `cwd=agent_pi.host_cwd()` to the `pi auth check` call. Take the folder before the `try`, and turn a `sandbox.SandboxFailed` into `return [str(error)]`, as the container branch above it does.

### 3. `.claude/skills/sssf/templates/container/pi_token_broker.py`

The broker is a standalone script and can't import `adw_modules`. Add a small `host_cwd()` of its own with the same path, variable and checks, returning `(folder, "")` or `("", message)`. `mint()` calls it first, returns `(False, message)` on a failure, and passes `cwd=folder` to `subprocess.run`. Argv unchanged. Add to the module docstring that pi starts there. A comment in each file names the other, so the two copies stay in step.

### 4. `.claude/skills/sssf/tests/conftest.py`

Add an autouse, function-scoped fixture that makes an empty folder under `tmp_path_factory` and `monkeypatch.setenv("SSSF_PI_HOST_CWD", ...)`. Without it every test that reaches a host start would try to make `~/.local/state/sssf-pi-host`, which agentsh denies, and tests that set `HOME` to a path that doesn't exist would fail.

### 5. `.claude/skills/sssf/tests/test_agent_pi.py`

Add, building any config from the shipped `templates/sssf.config.yaml` and never a test-only one:

- **The planted file.** A helper makes a temp repo (`git init`) with `.pi/settings.json` holding `{"packages": ["npm:sssf-probe"], "npmCommand": ["sh", "-c", "touch <marker>"]}`, and a fake `pi` that appends its argv and `pwd -P` to a log and answers a model list or a token. With `monkeypatch.chdir(repo)`:
  - `_pi_catalog()`: the logged `cwd` is the host folder and not inside the repo, argv has `--list-models`, every `HOST_FLAGS` member and `--no-approve`. No marker.
  - `_host_token("openai")`, `_host_token("google", check=True)`, `vm_token()` and `_sign_in({}, "openai")`: each logged `cwd` is the host folder; argv is exactly the baseline's.
  - A baseline twin, to show the test bites: the same fake `pi` run with the baseline's argv (`["--list-models", *HOST_FLAGS]`) and no `cwd=` logs the repo as its `cwd` and no `--no-approve`.
- **The rows.** With one fake `pi` output of several rows (a custom provider, a `1.0M` count, a short line), `_pi_catalog()` returns exactly the list the baseline's parser gives; write the expected rows out literally.
- **The folder.** With `SSSF_PI_HOST_CWD` unset and `HOME` at a temp folder: `host_cwd()` makes `<home>/.local/state/sssf-pi-host` with mode `0700` and returns it. A file in it, a link in its place, and a plain file in its place each raise `SandboxFailed` naming the path, the fake `pi` is never started, and what was there is still there. `_pi_catalog` under a non-empty folder raises and caches nothing.
- **No policy lets an agent write it.** No pattern in `agentsh_guard.PI_WRITE` or `agentsh_guard.CLAUDE_WRITE`, with `${HOME}` filled in, matches `<home>/.local/state/sssf-pi-host` or a file in it (`fnmatch`).
- **Agent calls are unchanged.** With the `own` fixture, the agent's `cmd` has no `--no-approve`, and `agentsh_guard.start` gets `cwd == request.cwd`. Extend the fixture's `start` wrapper to record `cwd`.
- **One real pi, no network.** `pytest.mark.skipif(shutil.which("pi") is None, ...)`. In a temp repo with the planted `settings.json` and a `.pi/commands/x.md`, with env `HOME=<temp home>`, `PI_CODING_AGENT_DIR=<empty temp agent dir>`, `PI_OFFLINE=1`, `SSSF_PI_HOST_CWD` unset: run the real `pi` with `_pi_catalog`'s own argv plus `--offline` and `cwd=agent_pi.host_cwd()`, timeout 60. Assert no marker, `.pi/commands` still there, no `.pi/prompts`, and nothing new in the repo. Don't assert the exit code or the rows: an empty agent dir may list nothing. Nothing touches `~/.pi/agent`, since both `HOME` and `PI_CODING_AGENT_DIR` are temp.
  - **What the baseline does here, from source.** With `--offline`, pi skips the install of a missing package (`package-manager.js:1014-1016`), so the marker is **not** made on the baseline's argv and `cwd` either, even in a project pi trusts. Without `--offline`, in a trusted project, it is. So the marker half of this test is a floor, and the engineer's proof covers the rest. The half that bites offline is the migration: on the baseline's argv and `cwd`, pi renames `.pi/commands` to `.pi/prompts`. Add that as a control in the same test: a second temp repo, the baseline's argv plus `--offline`, `cwd` the repo, and assert `.pi/prompts` now exists. Run it once. If the control does not behave as the source says, take the control out, keep the head's assertions, and say so in the envelope.
  - If the real `pi` can't start under agentsh, mark the test `host_only` with the comment form the other tests use, and say so in the envelope.

### 6. `.claude/skills/sssf/tests/test_trial.py` and `.claude/skills/sssf/tests/test_pi_token_broker.py`

- `test_trial.py`: one test that `pi_problems("google")` passes `cwd == agent_pi.host_cwd()` to `subprocess.run` (capture the keyword), and that a non-empty host folder comes back as one problem string, not a traceback.
- `test_pi_token_broker.py`: extend the `box` fixture's fake `pi` to log `pwd -P`, and start the broker with `SSSF_PI_HOST_CWD` at an empty temp folder and its own `cwd` in a temp repo with the planted file. Assert the logged `cwd` is the host folder and the argv is the pinned one. A second case: a file in the host folder gives an `ERR` line that names the folder and the fake `pi` is never started. Mark them `host_only` like their neighbours if they need the socket.

### 7. Docs

- `docs/open.md`: delete the row "A project's own `.pi/settings.json` can run a command in the host's `pi --list-models`". Nothing of it is left: the residue is the ignored-files hole, which has its own row.
- `.claude/skills/sssf/references/config.md`, line 847, "Discovery is off": add that every pi SSSF starts on the host (the model list, `pi auth print-bearer-token`, `pi auth check`, the container's token broker) runs in `~/.local/state/sssf-pi-host`, an empty folder SSSF makes and checks, never in the project, and that the model list also gets `--no-approve`; so the host's pi reads no project `.pi/settings.json`, `.pi/` resource, `.agents/skills`, `AGENTS.md` or `CLAUDE.md`, and runs no migration on the project. Say SSSF stops if the folder isn't empty and how to clear it, and name `SSSF_PI_HOST_CWD`. Say agents keep the repo as `cwd` and get no `--no-approve`.
- Same file, line 848, "What `--no-extensions` leaves running": cut "and a trusted project's `.pi/settings.json` (`docs/open.md`)". For the host it is now the engineer's own `~/.pi/agent/settings.json` only.
- Docstrings and comments in `agent_pi.py`, `trial.py` and `pi_token_broker.py`, as in steps 1 to 3.
- The repo keeps no CHANGELOG. The README says nothing about where the host's pi starts.
- **For the documenter.** `app_docs/`'s write-up of guard run `cd9eb718` (wrapped pi agents get their own agent dir) says the host's pi is started unwrapped from the orchestrator's folder. After this change that describes old behaviour; note it in the new write-up. No builder step touches `app_docs/`.

## Verification

Run from `.claude/skills/sssf`, under agentsh, this file only:

```
uv run --no-project -q --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py -m 'not pushes and not host_only' -q -p no:cacheprovider
```

- Every test in `tests/test_agent_pi.py` passes, the new ones and the existing argv tests.
- Don't run the whole suite under agentsh; verify runs it on the host (`docs/open.md`, the event read). The repo's own quality command runs `pytest tests`, which covers `tests/test_trial.py`, `tests/test_pi_token_broker.py`, `tests/test_sandbox.py`, `tests/test_rules.py` and `tests/test_container.py`. The plan adds no check of its own.
- Don't run a real pi against `~/.pi/agent`, and write nothing under `~/.pi`. The one real-pi test sets both `HOME` and `PI_CODING_AGENT_DIR` to temp folders.
- By reading: `grep -n "PI_PATH\|PI," .claude/skills/sssf/templates` shows every host start with `cwd=`; `agent_pi.run`'s `cmd` and `cwd=request.cwd` are as at `43b866d`.

## Out of scope

Old holes this change leaves open, to be filed as old:

- The ignored-files hole that gets a file into `.pi/` (`docs/open.md`, "The after-call check can't see gitignored files").
- The starter harness's pi subagents with no text check (its own row).
- What a wrapped pi agent reads from the project: its `.pi/settings.json` `packages` and `npmCommand`, skills, prompts and context files, inside the wrap.
- Any run's pi agent writing any run's agent dir under `~/.local/state/sssf-pi` (its own row).
- The engineer's own `~/.pi/agent/settings.json` `packages`, which the host's model list still resolves.
- With `local_guard: none`, an agent that writes `~/.local/state/sssf-pi-host`, `~/.pi/agent` or the `pi` on `PATH`.
- The agentsh policies. None changes.

## For the engineer

Nothing in this plan needs a path the builder can't write: `guard_builder`'s `writes` names `.claude/skills/sssf/`, and `docs/open.md` is not protected.

After the merge, the proof:

1. Update the hub's own install and commit it.
2. In a scratch repo with the planted `.pi/settings.json` and the hub's pi trusted for it, run the host's model list the way `_pi_catalog` does (from `~/.local/state/sssf-pi-host`, with `HOST_FLAGS` and `--no-approve`, no `--offline`) and see no marker.
3. Run one pi-roster call through `adw_prompt.py` and see it work.
