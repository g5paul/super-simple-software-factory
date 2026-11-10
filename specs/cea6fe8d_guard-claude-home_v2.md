# Keep Claude Code agents out of the engineer's own Claude Code files

Item 13 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Task: `specs/tasks/guard-agentsh-claude-home.md`. The change applies to Claude Code agents under `local_guard: agentsh` only:

- Each run gets its own Claude Code config folder.
- Agents sign in with a `claude setup-token` token.
- The policy closes `~/.claude` and `~/.claude.json`, and makes Claude Code's program read-only.

This is the second plan. It answers spec review 1: there is no new `LocalGuard` field and no new `session_files` helper.

All code paths below are in the skill source, `.claude/skills/sssf/templates/adws/adw_modules/`, written `adw_modules/` for short. The tests are in `.claude/skills/sssf/tests/`. Neither is in `protected_files`, which covers the repo-root `adws/` copy. This run doesn't touch that copy.

## Baseline

`main` at `30ffaf7` ("Write the guard spec that keeps agents out of ~/.claude"). Every finding is judged against it.

At the baseline:

- The claude policy's `sssf-agent-write` (`CLAUDE_WRITE`) allows every operation on these paths:
  - `${HOME}/.claude` and `${HOME}/.claude/**`
  - `${HOME}/.claude.json` and `${HOME}/.claude.json.*`
  - `${HOME}/.cache/claude-cli-nodejs/**`
  - `${HOME}/.local/state/claude/**`
  - `${HOME}/.local/share/claude/**`
  - `/run/user/*/cc-socks{,/**}`
- `sssf-agent-config` denies writes to nine paths in `~/.claude`.
- `CLAUDE_READ` allows `~/AGENTS.md`, `~/CLAUDE.md`, `~/CLAUDE.local.md`, `~/.config/anthropic{,/**}`, `~/github/skills/**`, `~/github/plugins/**` and the WSL managed-settings folder.
- Claude Code agents use the engineer's `~/.claude` login and config, and `check_login` runs `claude auth status` against them.

## Threat model

Agents run as the engineer's own user. Under the wrap, the agentsh policy is the only thing between an agent and the engineer's files. Outside the wrap, SSSF's runner and the engineer's own Claude Code run with full rights. So when a process outside the wrap later loads or runs a file an agent could write, that is agent code run outside the wrap.

What an agent can do at the baseline, and this change closes:

- Write the files the engineer's Claude Code runs or loads:
  - `~/.local/share/claude/versions/*`, the program `~/.local/bin/claude` points to
  - `~/.claude/plugins/`
  - `~/.claude/shell-snapshots/`
  - files next to `~/.claude/settings.json`
- Read `~/.claude/.credentials.json`, the full OAuth login with its refresh token.
- Read other repos' sessions:
  - their transcripts, in `~/.claude/projects/`
  - their file history
  - `~/.claude/sessions/<pid>.json` and `~/.claude/jobs/`
  - the `projects` map in `~/.claude.json`

What this guard can't stop at its level:

- **The run's own config folder is agent-writable.** It sits under `<data_root>/sessions/`, which every agent of the repo may write (`sssf-sessions`, decided 2026-11-09). An agent can read and change the config folder of any run of the same repo, transcripts included, and plant a `settings.json` there.
  - Agents load that folder only under `--safe-mode`, inside the wrap. There, the probes showed a planted `SessionStart` and `UserPromptSubmit` hook doesn't fire.
  - A link an agent plants there is judged by the policy at its target, because the engine resolves links (design doc, lines 202 and 471). So a link to `~/.claude` gets the agent nothing.
- **The runner never runs Claude Code outside the wrap on that folder.** `check_login` runs `claude auth status` outside the wrap and without `--safe-mode`, and a planted `apiKeyHelper` would run there. So `check_login` uses a private temp config folder of its own, with the same token and `DISABLE_AUTOUPDATER=1` (Step 3).
- **The token reaches the agent's Claude Code process.** The probes showed its Bash tool doesn't see it, and `sssf-proc-secrets` refuses `/proc/*/environ`. Code the agent runs inside the Claude Code process itself could still read it. The token only runs inference, for about a year, and never carries the refresh token. That is less than the `~/.claude/.credentials.json` login agents could read at the baseline.
- **`/proc/<pid>/cwd` and `cmdline`** still show the folders other processes run in.
- **Locks and logs stay writable.** `~/.local/state/claude/**` and `~/.cache/claude-cli-nodejs/**` stay open (Step 1), so an agent can delete or change Claude Code's lock files and logs there. Neither holds code.

## Bar

Against the baseline, under the rendered claude policy, a Claude Code agent under `local_guard: agentsh`:

- **can't read** these, and **can't list** `~/.claude`:
  - `~/.claude/.credentials.json` and `~/.claude.json`
  - `~/.claude/projects/**`, `~/.claude/file-history/**`, `~/.claude/sessions/**` and `~/.claude/jobs/**`
  - `~/.config/sssf-sandbox/token.env`
- **can't create, write, rename or delete** anything:
  - under `~/.claude`
  - at `~/.claude.json` or `~/.claude.json.*`
  - under `~/.local/share/claude`, `versions/2.1.322` included
- **still runs** each of these, under each repo's policy:
  - a call with a Bash tool call and a Read
  - a `--resume` of that call
  - a subagent

Beyond the agent itself:

- A run with no token refuses with the `claude setup-token` message, before any worktree or run row exists, and starts no agent.
- A pi agent's policy doesn't change, byte for byte. The text guard and VM runs don't change.
- The change must never keep an agent file, run agent code, or delete or overwrite an engineer file, anywhere the baseline does otherwise. In particular, the runner never runs Claude Code outside the wrap on an agent-writable config folder.

## Out of scope

These are old holes, or limits the task accepts:

- Runs under `local_guard: text`, which keep the engineer's login and `~/.claude`.
- VM runs, which already sign in with the token. `sandbox_remote.py` is unchanged.
- pi's policy, and `sssf-other-sign-in`'s pi entry.
- `/proc/<pid>/cwd` and `cmdline`.
- Shared writes across one repo's runs:
  - the agent cache;
  - `sssf-sessions`, which lets one run's agents read and write another run's session folder, its `claude-config/` included.
- `~/.local/state/claude/**` and `~/.cache/claude-cli-nodejs/**` staying writable: they hold locks and logs, not code.
- `CLAUDE.local.md` and the WSL managed-settings path, which stay in `CLAUDE_READ`. No probe settled whether Claude Code still reads them, so the hub's post-merge probe records it.
- `sssf-agent-config`, which stays. Default-deny now covers it too, but removing it isn't one of the six changes.
- `trial.py`'s `agent_cc.check_login()` call, which keeps its baseline behaviour, with no guard passed.

## Steps

### Step 1. The policy (`adw_modules/agentsh_guard.py`)

Change only the module-level lists. `file_rules` keeps its rule order.

- **`CLAUDE_WRITE`** drops `.claude`, `.claude/**`, `.claude.json`, `.claude.json.*` and `.local/share/claude/**`. It becomes:
  `[f"${{HOME}}/{p}" for p in (".cache/claude-cli-nodejs/**", ".local/state/claude/**")] + ["/run/user/*/cc-socks", "/run/user/*/cc-socks/**"]`
- **`CLAUDE_READ`** drops `AGENTS.md`, `CLAUDE.md`, `github/skills/**` and `github/plugins/**`, and adds `.local/share/claude/**`. It becomes `CLAUDE.local.md`, `.config/anthropic`, `.config/anthropic/**` and `.local/share/claude/**`, plus the two `/mnt/c/Program Files/ClaudeCode` entries.
  - `READ` is `open, read, stat, list, readlink, access`. That still lets agents run `~/.local/bin/claude` → `versions/<v>`, the way execs under `/usr/**` already work under `READ` alone.
- **The comments above the two lists** carry the evidence:
  - `~/.local/share/claude` holds the program (`versions/`), which the engineer's `claude` runs. So it is read-only, and `DISABLE_AUTOUPDATER=1` keeps the agents' Claude Code from trying to update it.
  - `~/.local/state/claude` holds lock files: the 2026-11-10 probe's only writes outside the config folder were to `locks/`. `~/.cache/claude-cli-nodejs` holds Claude Code's logs. Claude Code loads no code from either, so both stay writable.
  - `~/CLAUDE.md`, `~/AGENTS.md`, `~/github/skills` and `~/github/plugins` went unread in the 2026-11-10 probe under `--safe-mode`. `~/.config/anthropic` was read.
- After these edits, only two rules still name `~/.claude` or `~/.claude.json`, and both are denies: `sssf-other-sign-in`/`CLAUDE_SIGN_IN` (pi's policy) and `sssf-agent-config`. So default-deny refuses every operation there, reads and listing included. `sssf-credentials` already denies `~/.config/sssf-sandbox/**`.

Add these beside `cache_env`, in the same module:

```python
TOKEN_FILE = Path.home() / ".config" / "sssf-sandbox" / "token.env"   # the file --sandbox reads
CLAUDE_HOME = "claude-config"   # the run's Claude Code config folder, in its session folder
NO_TOKEN = ("Claude Code agents under local_guard: agentsh sign in with a token, and none was found. "
            "Run `claude setup-token` in a terminal and save CLAUDE_CODE_OAUTH_TOKEN=<token> in "
            "~/.config/sssf-sandbox/token.env, mode 600 (or export CLAUDE_CODE_OAUTH_TOKEN where the ADW "
            "is launched). The token can only run inference, for about a year; agents never get your "
            "login's refresh token.")

def claude_token(environ=None) -> str:
    """CLAUDE_CODE_OAUTH_TOKEN from the launching shell, else from TOKEN_FILE; "" when neither has one."""

def claude_env(guard, token: str, config_dir: str) -> dict[str, str]:
    """What a Claude Code agent under agentsh needs: its own config folder, the token, no auto-update.
    {} unless guard is an agentsh guard."""
```

- **`claude_token`**:
  - Uses `environ = os.environ if environ is None else environ`, and returns the stripped `environ.get("CLAUDE_CODE_OAUTH_TOKEN")` if it's non-empty.
  - Otherwise it reads `TOKEN_FILE` as a module attribute at call time, so tests can monkeypatch it. A missing or unreadable file (`OSError`) counts as no token.
  - It parses the file the way `sandbox_remote._token` does: the first `CLAUDE_CODE_OAUTH_TOKEN=` line with a non-empty value, stripped of whitespace and of `"` or `'`.
  - It returns `""` when it finds nothing. It never raises and never logs the value.
  - Don't change `sandbox_remote.py`.
- **`claude_env`**:
  - Returns `{}` when `guard is None or guard.mode != "agentsh"`.
  - Raises `AgentshRefused(NO_TOKEN)` when `token` is empty.
  - Otherwise returns `{"CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token, "DISABLE_AUTOUPDATER": "1"}`.

### Step 2. The early refusal (`adw_modules/session.py`)

In `session.ensure`, inside the existing `if guard.mode == "agentsh" and not sandbox_name and not _vm_resume(cfg, adw_id):` block, right after `check_machine`. That is before `worktree.launching()`, so no worktree or run row exists yet:

```python
if "claude_code" in guard.policies and not agentsh_guard.claude_token():
    raise SystemExit(agentsh_guard.NO_TOKEN)
```

`guard_for` keys `policies` by harness, and only for the roster's harnesses, so a pi-only roster never needs a token. Nothing else in `session.py` changes, and `LocalGuard` and `data_types.py` don't change.

### Step 3. The agent's environment (`adw_modules/agent_cc.py`, `adw_modules/agents.py`)

**`agent_cc.child_env(agent_name="", adw_id="", allowed=(), *, guard=None, config_dir="")`**

- After the subscription key pops, add:
  `env.update(agentsh_guard.claude_env(guard, agentsh_guard.claude_token(env) if guard is not None and guard.mode == "agentsh" else "", config_dir))`
  The token comes from the operator env first, then the file. This line also overrides any `CLAUDE_CONFIG_DIR` the engineer's shell had.
- With no guard, it returns exactly what it returns today.
- Update the docstring.

**`agent_cc.run`**

- Move `tree = sandbox.current()` above the `child_env` call.
- When `tree is sandbox.LOCAL` and `request.local_guard` is an agentsh guard:
  1. Work out `config_dir = os.path.abspath(Path(request.data_root) / "sessions" / request.adw_id / agentsh_guard.CLAUDE_HOME)`. The run's session folder is `data_root/sessions/<adw_id>` (`runner.py:57`), so every call of the run, and every rejoining process, gets the same folder, and a retry's `--resume` finds its transcript.
  2. Make sure the folder exists with the existing link-safe walk: `session_files.write(Path(config_dir) / ".sssf", "", request.data_root)`. That walk makes each component a real folder, replacing an agent's link, file or FIFO, and writes an empty marker of SSSF's. Probes so far used a folder that already existed, so don't rely on Claude Code to create it.
  3. Pass `guard=request.local_guard, config_dir=config_dir` to `child_env`.
- Otherwise, including on a VM tree, pass no guard, so a host path never reaches a VM.
- An `AgentshRefused` raised here takes the same path as one from `agentsh_guard.start`.

**`agent_cc.check_login(guard=None)`**

- When `guard` is an agentsh guard:
  - Make a private folder with `tempfile.TemporaryDirectory(prefix="sssf-claude-login-")`.
  - Build the env with `child_env(guard=guard, config_dir=<that folder>)`, run `claude auth status` with it, and remove the folder afterwards.
  - Don't use the run's folder here. That folder is agent-writable, and this call runs outside the wrap without `--safe-mode`, so a planted `settings.json` (`apiKeyHelper`, `env`) would run agent code outside the wrap.
- Turn an `AgentshRefused` from `child_env` into `NotLoggedIn(str(error))`.
- Under agentsh, when the status says it isn't logged in, the message says the token didn't sign in, and names `claude setup-token` and `~/.config/sssf-sandbox/token.env`, mode 600.
- The text guard keeps the baseline message, and `_logged_in` stays once per process.

**`agents.execute`**

Call `agent_cc.check_login(run.local_guard if sandbox.current() is sandbox.LOCAL else None)`. Import `sandbox` if the module doesn't already.

**`agent_cc`'s module docstring**

Replace the "Auth stays on the operator's …" sentence with:

- Under `local_guard: agentsh`, Claude Code agents run from the run's own `CLAUDE_CONFIG_DIR`, `sessions/<adw_id>/claude-config`, and sign in with `CLAUDE_CODE_OAUTH_TOKEN`.
- Under the text guard, auth stays on the operator's login.

### Step 4. The design's deny-policy script (`docs/designs/agentsh/deny-policy.py`)

`test_file_rules_equal_the_designs_deny_policy_output` compares the rendered file rules with this script's output. Make the same `CLAUDE_READ` and `CLAUDE_WRITE` edits there, with the same comments, and nothing else.

### Step 5. Docs (`.claude/skills/sssf/references/config.md`)

In "Running local agents under agentsh — `local_guard`", add the points below. Promise no more than the probes show:

- **Their own folder.** Claude Code agents run from their own config folder, `adws/adw_data/sessions/<adw_id>/claude-config/`, one per run, and a retry's `--resume` reuses it.
- **A token.** They sign in with a token from `claude setup-token`, read from `CLAUDE_CODE_OAUTH_TOKEN` in the launching shell, or else from `~/.config/sssf-sandbox/token.env` (`CLAUDE_CODE_OAUTH_TOKEN=<token>`, mode 600), the file `--sandbox` reads.
  - A run with neither refuses before it starts.
  - The token can only run inference, and it lasts about a year.
  - Agents never get the login's refresh token, and their Bash calls don't see the token.
- **What they can't touch.** They can't read or write `~/.claude` or `~/.claude.json`. They can read Claude Code's program in `~/.local/share/claude` but not change it, and SSSF sets `DISABLE_AUTOUPDATER=1` for them. `~/.local/state/claude` (locks) and `~/.cache/claude-cli-nodejs` (logs) stay writable.
- **Who can change the folder.** A run's agents, and those of the repo's other runs, can change that folder. Agents load it under `--safe-mode`, where a planted hook doesn't fire.
- **The text guard.** Under the text guard, agents keep your login and `~/.claude`.
- **Reinstall.** After this update, reinstall with `just agentsh-install` and run the sudo lines it prints.

In the limits bullet ("Under agentsh an agent can list …", near line 484):

- Drop "plus the folders it may read in your home, such as `~/.claude` for Claude Code". Use a true example or none.
- Drop the sentence "A Claude Code agent may read all of `~/.claude` and `~/.claude.json` … a secret you pasted into one included."
- Keep "`/proc/<pid>/cwd` and `cmdline` show the folders your other processes run in." and the `stat`/`inotify` sentence.

Don't claim that a subagent or `--resume` is proven under the new policy. The hub probes both after the merge.

Other docs:

- `docs/designs/agentsh-local-sandbox.md` Next item 13: leave it. The hub records the post-merge probes there.
- There is no CHANGELOG, and `README.md` doesn't describe `~/.claude` under agentsh.
- `app_docs/`: don't edit it. **For the documenter:** any write-up there that says Claude Code agents under agentsh read or write `~/.claude`, `~/.claude.json` or `~/.local/share/claude`, or read `~/github/skills`, now describes the old behaviour.

### Step 6. Tests

**`.claude/skills/sssf/tests/test_agentsh_guard.py`**

Configs come from the shipped `templates/sssf.config.yaml` (`SHIPPED`, the `cfg` fixture). Every token test does two things:

- monkeypatches `agentsh_guard.TOKEN_FILE` to a file under `tmp_path`;
- calls `monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)` unless it sets the variable itself.

Never touch the real `~/.config/sssf-sandbox/token.env`.

1. **`test_claude_policy_closes_the_engineers_claude_code_files`.** Render `policy_text(RepoSettings(), "/m/proj/.git", "claude_code", "n", **PATHS)`, `yaml.safe_load` it, and walk `file_rules`. Assert:
   - No `decision: allow` rule has a path equal to or under `${HOME}/.claude`, or starting with `${HOME}/.claude.json`.
   - `sssf-agent-write` has no path under `${HOME}/.claude`, `${HOME}/.claude.json` or `${HOME}/.local/share/claude`.
   - The only allow rule naming `${HOME}/.local/share/claude` is `sssf-agent-read`, and its operations equal `agentsh_guard.READ`.
   - `sssf-agent-read` has none of `${HOME}/github/skills/**`, `${HOME}/github/plugins/**`, `${HOME}/CLAUDE.md` or `${HOME}/AGENTS.md`.
   - `${HOME}/.config/sssf-sandbox/**` is in `sssf-credentials` (deny, `*`), ahead of every allow.
2. **`test_pi_policy_keeps_its_shape`.** This is the shape check only. In the pi policy:
   - the `sssf-agent-write` paths equal `PI_WRITE`;
   - there is no `sssf-agent-read`;
   - `sssf-other-sign-in` equals `CLAUDE_SIGN_IN`.

   The byte-for-byte pin is Verification step 3's diff against `30ffaf7`.
3. **`test_claude_token_from_the_environment_then_the_file`.**
   - With the env var set, the token comes from it.
   - With the env var unset and a temp file holding `CLAUDE_CODE_OAUTH_TOKEN="tok"`, it returns `tok`.
   - A missing file, or a file without the line, gives `""`.
4. **`test_ensure_refuses_without_a_token_before_a_worktree_or_run_row`.** Use the `machine` fixture and the pattern of `test_ensure_refuses_a_missing_service_with_no_traceback` and `test_each_machine_refusal_fires_before_an_agent_starts`; read them first. With no token and the shipped roster, which has a Claude Code agent:
   - `session.ensure` raises `SystemExit`, and its text contains `claude setup-token` and `token.env`.
   - No worktree, no `sessions/<adw_id>` folder and no run row was made. Assert this the way the neighbouring refusal test does.
5. **`test_claude_code_child_env_under_agentsh`.** Use `machine.guard`, a temp token file and `config_dir=str(tmp_path / "data" / "sessions" / "abc" / "claude-config")`.
   - `agent_cc.child_env(guard=machine.guard, config_dir=config_dir)` has `CLAUDE_CONFIG_DIR == config_dir`, `CLAUDE_CODE_OAUTH_TOKEN` from the file, and `DISABLE_AUTOUPDATER == "1"`.
   - With the env var set, the env var wins.
   - With an engineer `CLAUDE_CONFIG_DIR=/home/u/.claude` in `os.environ`, the run's folder wins.
   - A text guard, or `guard=None`, gives no `CLAUDE_CONFIG_DIR` and no `DISABLE_AUTOUPDATER`.
   - With no token, `child_env` under the agentsh guard raises `AgentshRefused` matching `claude setup-token`.
6. **`test_a_claude_agent_gets_the_runs_config_folder_and_refuses_without_a_token`.** Drive `agent_cc.run` under `machine.guard` on `sandbox.LOCAL`, with the `fake` and `request` helpers already imported from `test_agent_cc`. Give it a `request` whose `data_root` is under `tmp_path`, and `adw_id="abc"`.
   - With a temp token, the fake wrap's `FAKE_DUMP` env has `CLAUDE_CONFIG_DIR == <data_root>/sessions/abc/claude-config`, that folder exists as a real folder, and `DISABLE_AUTOUPDATER == "1"`.
   - With no token, the run raises `AgentshRefused`, and `FAKE_DUMP` is never written.

**`.claude/skills/sssf/tests/test_agent_cc.py`**, beside the `auth` fixture tests:

7. **`test_check_login_under_agentsh_uses_the_token_and_a_private_folder`.** Make the fake `claude` (`FAKE_AUTH` or a variant) write its `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN` and `DISABLE_AUTOUPDATER` to a temp file, and use a temp `TOKEN_FILE`.
   - The token and `DISABLE_AUTOUPDATER=1` reach the check.
   - `CLAUDE_CONFIG_DIR` is not under any `sessions/` folder, and it is gone after the call.
   - With no token, the check raises `NotLoggedIn`, mentioning `claude setup-token`.
   - With no guard, `check_login()` behaves as before, so the existing two tests pass unchanged.

Update existing tests only where they assert the old lists. Search for `CLAUDE_WRITE`, `CLAUDE_READ`, `github/skills`, `.claude.json` and `local/share/claude`.

## Verification

This run is under agentsh, and the builder's own Claude Code still signs in the old way. The new code reaches the runner only after the merge.

1. **The two test files.** As in runs 98e91ed7 and 47b4d0ea, copy `.claude` and `docs/designs/agentsh` to a fresh `/tmp` folder, keeping their relative paths so `DENY_POLICY` resolves. Then run:
   `uv run --no-config --directory <copy>/.claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py tests/test_agent_cc.py -q -p no:cacheprovider`
   Every new test passes, and so does `test_file_rules_equal_the_designs_deny_policy_output` for both harnesses.
2. **The whole suite.** Run the whole `tests` folder the same way. Wrap denials cause known failures (run 6e904be8: 172 failed, 12 errors), so compare your count with that, and report any new failure in `test_agentsh_guard.py` or `test_agent_cc.py`. The repo's own test check decides. SSSF's quality step runs it outside the wrap:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. **The rendered policies.** In the copy, render the claude and pi policies with `python -c`, calling `agentsh_guard.policy_text` as test 1 does.
   - In the claude policy, `${HOME}/.claude` and `${HOME}/.claude.json` appear in no allow rule, and `.local/share/claude` appears only under `sssf-agent-read`.
   - Extract `30ffaf7`'s `agentsh_guard.py` with `git show 30ffaf7:<path>` into a temp folder beside the copy's other modules. Render the pi policy from it the same way, and `diff` it against the pi policy you just rendered. They must be byte-identical; this is the pi pin.
4. **Untouched paths.** Nothing under `app_docs/`, the repo-root `adws/` or `specs/` is touched.

After the merge, which isn't the builder's job: the engineer reinstalls both repos' policies with `just agentsh-install`. The hub session reruns the probes under the wrap, in the hub and in sssf-sandbox, with new decoy files only. Then it runs a real chain in sssf-sandbox under `local_guard: agentsh`. The probes cover:

- the Bash and Read call, `--resume` and a subagent;
- the reads and writes the bar lists;
- whether `~/CLAUDE.local.md` and the WSL path are still opened;
- `claude auth status` on a fresh folder with only `CLAUDE_CODE_OAUTH_TOKEN`, outside the wrap. It must report `loggedIn: true`, or every agentsh run refuses;
- any refused `stat` or `list` on `~/.local/share/claude` itself, since `.local/share/claude/**` may not match the folder.

## Notes for the reviewer

- **One deliberate departure from the task's wording.** The task says `check_login` runs with the run's `CLAUDE_CONFIG_DIR`. Here it gets the same token and `DISABLE_AUTOUPDATER=1`, but a private temp config folder. The run's folder is writable by every agent of the repo, and `claude auth status` runs outside the wrap without `--safe-mode`, so a planted `apiKeyHelper` there would be agent code run outside the wrap.
- **Changes since spec review 1.** There is no `LocalGuard.claude_config` and no `session_files.own_dir`. `agent_cc.run` works out the folder from `request.data_root` and `request.adw_id`, and creates it with the existing `session_files.write` walk.
