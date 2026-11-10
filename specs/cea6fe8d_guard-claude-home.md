# Keep Claude Code agents out of the engineer's own Claude Code files

Item 13 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Task: `specs/tasks/guard-agentsh-claude-home.md`. For Claude Code agents under `local_guard: agentsh` only. Each run gets its own Claude Code config folder. Agents sign in with a `claude setup-token` token. The policy closes `~/.claude`, `~/.claude.json` and writes to Claude Code's program.

All code paths below are in the skill source, `.claude/skills/sssf/templates/adws/adw_modules/`, written `adw_modules/` for short. None of them is in `protected_files`. That list covers the repo-root `adws/` copy, which this run doesn't touch. The tests are in `.claude/skills/sssf/tests/`, which isn't protected either.

## Baseline

`main` at `30ffaf7` ("Write the guard spec that keeps agents out of ~/.claude"). Every finding is judged against it. At the baseline, the claude policy's `sssf-agent-write` (`CLAUDE_WRITE`) allows every operation on `${HOME}/.claude`, `${HOME}/.claude/**`, `${HOME}/.claude.json`, `${HOME}/.claude.json.*`, `${HOME}/.cache/claude-cli-nodejs/**`, `${HOME}/.local/state/claude/**`, `${HOME}/.local/share/claude/**` and `/run/user/*/cc-socks{,/**}`. `sssf-agent-config` denies writes to nine paths in `~/.claude`. `CLAUDE_READ` allows `~/AGENTS.md`, `~/CLAUDE.md`, `~/CLAUDE.local.md`, `~/.config/anthropic{,/**}`, `~/github/skills/**`, `~/github/plugins/**` and the WSL managed-settings folder. Claude Code agents use the engineer's `~/.claude` login and config, and `check_login` runs `claude auth status` against them.

## Threat model

Agents run as the engineer's own user. Under the wrap, the agentsh policy is the only thing between an agent and the engineer's files. Outside the wrap, SSSF's runner and the engineer's own Claude Code run with full rights. So any file an agent can write that a process outside the wrap later loads or runs is agent code run outside the wrap.

What an agent can do at the baseline, and this change closes:

- Write `~/.local/share/claude/versions/*`, the program `~/.local/bin/claude` points to, and `~/.claude/plugins/`, `~/.claude/shell-snapshots/` and files next to `~/.claude/settings.json`. The engineer's Claude Code runs or loads all of these.
- Read `~/.claude/.credentials.json`, the full OAuth login with its refresh token.
- Read other repos' transcripts (`~/.claude/projects/`), file history, `~/.claude/sessions/<pid>.json`, `~/.claude/jobs/`, and the `projects` map in `~/.claude.json`.

What this guard can't stop at its level, by design:

- The run's own config folder sits under `<data_root>/sessions/`, which every agent of the repo may write (`sssf-sessions`, decided 2026-11-09). An agent can read and change the config folder, transcripts included, of any run of the same repo, and plant `settings.json` there. Agents only load it under `--safe-mode`, where the probes showed a planted `SessionStart` and `UserPromptSubmit` hook doesn't fire. **The runner never runs Claude Code outside the wrap on that folder.** `check_login` runs `claude auth status` outside the wrap and without `--safe-mode`, and a planted `settings.json` with an `apiKeyHelper` would run there. So `check_login` uses a private temp config folder of its own, with the same token and `DISABLE_AUTOUPDATER=1` (see Step 3, and the note to the reviewer).
- The token reaches the agent's Claude Code process. The probes showed its Bash tool doesn't see it, and `sssf-proc-secrets` refuses `/proc/*/environ`, but code the agent runs in the same process could. The token is inference-only and lasts about a year, and it never carries the refresh token. It's a smaller grant than the `~/.claude/.credentials.json` login agents could read at the baseline.
- `/proc/<pid>/cwd` and `cmdline` still show the folders other processes run in.
- `~/.local/state/claude/**` and `~/.cache/claude-cli-nodejs/**` stay writable (see Step 1). An agent can delete or change Claude Code's lock files and logs there. They hold no code.

## Bar

Against the baseline, under the rendered claude policy, a Claude Code agent under `local_guard: agentsh`:

- **can't read** `~/.claude/.credentials.json`, `~/.claude.json`, `~/.claude/projects/**`, `~/.claude/file-history/**`, `~/.claude/sessions/**`, `~/.claude/jobs/**` or `~/.config/sssf-sandbox/token.env`, and **can't list** `~/.claude`;
- **can't create, write, rename or delete** anything under `~/.claude`, at `~/.claude.json` or `~/.claude.json.*`, or under `~/.local/share/claude`, `versions/2.1.322` included;
- **still runs**: a call with a Bash tool call and a Read, a `--resume` of it, and a subagent, under each repo's policy.

A run with no token refuses with the `claude setup-token` message before any worktree or run row exists, and starts no agent. A pi agent's policy doesn't change, byte for byte. The text guard and VM runs don't change. On top of that, the change must never keep an agent file, run agent code, or delete or overwrite an engineer file anywhere the baseline does otherwise. In particular, the runner never runs Claude Code outside the wrap on an agent-writable config folder.

## Out of scope

These are old holes, or limits the task accepts. A reviewer files them as such:

- Runs under `local_guard: text`: they keep the engineer's login and `~/.claude`.
- VM runs, which already sign in with the token. `sandbox_remote.py` is unchanged.
- pi's policy, and `sssf-other-sign-in`'s pi entry.
- `/proc/<pid>/cwd` and `cmdline`.
- The agent cache's shared writes across one repo's runs, and likewise `sssf-sessions`, which lets one run's agents read and write another run's session folder, its `claude-config/` included.
- `~/.local/state/claude/**` and `~/.cache/claude-cli-nodejs/**` stay writable: locks and logs, no code (Step 1).
- `CLAUDE.local.md` and the WSL managed-settings path stay in `CLAUDE_READ`, because no probe settled whether Claude Code still reads them. The hub's post-merge probe records it.
- `sssf-agent-config` stays as it is. It is now covered by default-deny, but removing it is not one of the six changes.
- `trial.py`'s `agent_cc.check_login()` call keeps its baseline behaviour, with no guard passed.

## Steps

### Step 1. The policy (`adw_modules/agentsh_guard.py`)

Edit the module-level lists only. `file_rules` keeps its rule order.

- `CLAUDE_WRITE`: drop `.claude`, `.claude/**`, `.claude.json`, `.claude.json.*` and `.local/share/claude/**`. It becomes `[f"${{HOME}}/{p}" for p in (".cache/claude-cli-nodejs/**", ".local/state/claude/**")] + ["/run/user/*/cc-socks", "/run/user/*/cc-socks/**"]`.
- `CLAUDE_READ`: drop `AGENTS.md`, `CLAUDE.md`, `github/skills/**` and `github/plugins/**`, and add `.local/share/claude/**`. It becomes `CLAUDE.local.md`, `.config/anthropic`, `.config/anthropic/**`, `.local/share/claude/**`, plus the two `/mnt/c/Program Files/ClaudeCode` entries. With `READ` (`open, read, stat, list, readlink, access`), agents can still run `~/.local/bin/claude` → `versions/<v>`. Execs of `/usr/**` already work under `READ` alone.
- Update the comments above the two lists. Say what each kept path is for and why, as below. Put the evidence in a comment on `CLAUDE_READ`/`CLAUDE_WRITE`:
  - `~/.local/share/claude` holds the program (`versions/`), which the engineer's `claude` runs. It is read-only, and `DISABLE_AUTOUPDATER=1` keeps the agents' Claude Code from trying to update it.
  - `~/.local/state/claude` holds lock files: the 2026-11-10 probe's only writes outside the config folder were `locks/`. `~/.cache/claude-cli-nodejs` holds Claude Code's logs. Claude Code loads no code from either, so both stay writable.
  - `~/CLAUDE.md`, `~/AGENTS.md`, `~/github/skills` and `~/github/plugins` went unread in the 2026-11-10 probe under `--safe-mode`. `~/.config/anthropic` was read.
- Nothing names `~/.claude` or `~/.claude.json` any more except `sssf-other-sign-in`/`CLAUDE_SIGN_IN` (pi's policy) and `sssf-agent-config` (deny). So default-deny refuses every operation there, reads and listing included. `~/.config/sssf-sandbox/**` is already denied by `sssf-credentials`.

Add, in the same module, beside `cache_env`:

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

- `claude_token`: `environ = os.environ if environ is None else environ`. Return the stripped `environ.get("CLAUDE_CODE_OAUTH_TOKEN")` if non-empty. Otherwise read `TOKEN_FILE`, and treat a missing or unreadable file (`OSError`) as no token. Parse it the way `sandbox_remote._token` does: the first `CLAUDE_CODE_OAUTH_TOKEN=` line with a non-empty value, stripped of whitespace and of `"`/`'`. Return `""` when nothing is found. Never raise, and never log the value. Read `TOKEN_FILE` at call time, as a module attribute, so tests can monkeypatch it. Don't change `sandbox_remote.py`.
- `claude_env`: return `{}` when `guard is None or guard.mode != "agentsh"`. Otherwise, with an empty `token`, raise `AgentshRefused(NO_TOKEN)`. With an empty `config_dir`, raise `AgentshRefused("no Claude Code config folder reached the launch; this is a bug in the caller")`. Otherwise return `{"CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token, "DISABLE_AUTOUPDATER": "1"}`.

### Step 2. The run's folder and the early refusal (`adw_modules/data_types.py`, `adw_modules/session.py`)

- `LocalGuard` gets `claude_config: str = ""` with the comment `# the run's own Claude Code config folder, under agentsh`. `guard_for` leaves it empty, so the install script's rendering is unchanged.
- `session.ensure`, in the existing block `if guard.mode == "agentsh" and not sandbox_name and not _vm_resume(cfg, adw_id):`, right after `check_machine`. This is before `worktree.launching()`, so no worktree or run row exists yet:

  ```python
  if "claude_code" in guard.policies and not agentsh_guard.claude_token():
      raise SystemExit(agentsh_guard.NO_TOKEN)
  ```
- Where `ensure` copies the guard with `root` (`guard.model_copy(update={"root": ...})`), also set `"claude_config": str(Path(run.session_dir) / agentsh_guard.CLAUDE_HOME)`. That gives one folder per run, which a retry or a rejoining process finds again, so `--resume` finds its transcript.

### Step 3. The agent's environment (`adw_modules/agent_cc.py`, `adw_modules/session_files.py`, `adw_modules/agents.py`)

- `session_files.py`: add the helper below. It walks from `root` and makes every component a real folder, replacing an agent's link, file or FIFO, as `_walk` already does. The runner calls it before handing the folder to the wrap.

  ```python
  def own_dir(path, root) -> None:
      """Make `path` a real folder under `root`, replacing any link on the way, as _walk does."""
      fd, _ = _walk(root, os.path.join(os.fspath(path), "_"))
      os.close(fd)
  ```
- `agent_cc.child_env(agent_name="", adw_id="", allowed=(), *, guard=None, config_dir="")`: after the subscription key pops, add `env.update(agentsh_guard.claude_env(guard, agentsh_guard.claude_token(env) if guard is not None and guard.mode == "agentsh" else "", config_dir))`. That reads the token from the operator env first, then the file, and it overrides any `CLAUDE_CONFIG_DIR` the engineer's shell had. Keep the signature backward compatible: with no guard it returns exactly what it returns today. Update the docstring.
- `agent_cc.run`: move `tree = sandbox.current()` above the `child_env` call. When `tree is sandbox.LOCAL and request.local_guard is not None and request.local_guard.mode == "agentsh"`, call `session_files.own_dir(request.local_guard.claude_config, request.data_root)` first. Only when `claude_config` is non-empty: an empty one reaches `claude_env`'s refusal. Then pass `guard=request.local_guard, config_dir=request.local_guard.claude_config` to `child_env`. On a VM tree, pass no guard, so a host path never reaches the VM. An `AgentshRefused` raised here takes the same path as one raised by `agentsh_guard.start`.
- `agent_cc.check_login(guard=None)`:
  - When `guard` is an agentsh guard, make a private folder with `tempfile.TemporaryDirectory(prefix="sssf-claude-login-")` and build the env with `child_env(guard=guard, config_dir=<that folder>)`. Run `claude auth status` with it, and remove the folder afterwards.
  - Don't use the run's `claude_config` here. It's agent-writable, and this call runs outside the wrap without `--safe-mode`, so a planted `settings.json` (`apiKeyHelper`, `env`) would run agent code outside the wrap.
  - Turn an `AgentshRefused` from `child_env` into `NotLoggedIn(str(error))`.
  - Under agentsh, when the status says not logged in, the message says the token didn't sign in. It names `claude setup-token` and `~/.config/sssf-sandbox/token.env`, mode 600. The baseline message stays for the text guard.
  - `_logged_in` stays once per process.
- `agents.execute`: call `agent_cc.check_login(run.local_guard if sandbox.current() is sandbox.LOCAL else None)`. Import `sandbox` if `agents.py` doesn't already.
- `agent_cc`'s module docstring: replace the "Auth stays on the operator's …" sentence. Under `local_guard: agentsh`, Claude Code agents run from the run's own `CLAUDE_CONFIG_DIR` and sign in with `CLAUDE_CODE_OAUTH_TOKEN`. Under the text guard, auth stays on the operator's login.

### Step 4. The design's deny-policy script (`docs/designs/agentsh/deny-policy.py`)

`test_file_rules_equal_the_designs_deny_policy_output` compares the rendered file rules with this script's output. Make the same `CLAUDE_READ` and `CLAUDE_WRITE` edits there, with the same comments. That is all that changes in the script.

### Step 5. Docs (`.claude/skills/sssf/references/config.md`)

In "Running local agents under agentsh — `local_guard`":

- Add these points, promising no more than the probes show:
  - Claude Code agents run from their own config folder, `adws/adw_data/sessions/<adw_id>/claude-config/`, one per run, which a retry's `--resume` reuses.
  - They sign in with a token from `claude setup-token`, read from `CLAUDE_CODE_OAUTH_TOKEN` in the launching shell or from `~/.config/sssf-sandbox/token.env` (`CLAUDE_CODE_OAUTH_TOKEN=<token>`, mode 600). That's the file `--sandbox` reads. A run with neither refuses before it starts. The token can only run inference, and lasts about a year. Agents never get the login's refresh token, and their Bash calls don't see the token.
  - They can't read or write `~/.claude` or `~/.claude.json`, and they can read but not change Claude Code's program in `~/.local/share/claude`. SSSF sets `DISABLE_AUTOUPDATER=1` for them. `~/.local/state/claude` (locks) and `~/.cache/claude-cli-nodejs` (logs) stay writable.
  - A run's agents, and those of the repo's other runs, can change that folder, and agents load it under `--safe-mode`, where a planted hook doesn't fire.
- Under the text guard, agents keep your login and `~/.claude`.
- Say that after this update the engineer reinstalls with `just agentsh-install` and runs the sudo lines it prints.
- In the limits bullet (line ~484, "Under agentsh an agent can list …"):
  - Drop "plus the folders it may read in your home, such as `~/.claude` for Claude Code". Use an example that is still true, or none.
  - Drop the sentence "A Claude Code agent may read all of `~/.claude` and `~/.claude.json` … a secret you pasted into one included."
  - Keep "`/proc/<pid>/cwd` and `cmdline` show the folders your other processes run in." and the `stat`/`inotify` sentence.
- Don't claim that the subagent and `--resume` paths are proven under the new policy. The hub probes them after the merge.

Other docs this makes out of date:

- `docs/designs/agentsh-local-sandbox.md` Next item 13: leave it. The hub session records the post-merge probes there.
- No CHANGELOG exists. `README.md` doesn't describe `~/.claude` under agentsh, so it doesn't change.
- `app_docs/`: don't edit. **For the documenter:** any write-up there that says Claude Code agents under agentsh read or write `~/.claude`, `~/.claude.json` or `~/.local/share/claude`, or read `~/github/skills`, now describes the old behaviour.

### Step 6. Tests

`.claude/skills/sssf/tests/test_agentsh_guard.py`: build the config from the shipped `templates/sssf.config.yaml`, through the existing `cfg` fixture. Every token test monkeypatches `agentsh_guard.TOKEN_FILE` to a file under `tmp_path`, and `monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)` unless the test sets it. Never touch the real `~/.config/sssf-sandbox/token.env`. Add:

1. `test_claude_policy_closes_the_engineers_claude_code_files`: render `policy_text(RepoSettings(), "/m/proj/.git", "claude_code", "n", **PATHS)`, `yaml.safe_load` it, and walk `file_rules`.
   - No rule with `decision: allow` has a path equal to or under `${HOME}/.claude`, or starting with `${HOME}/.claude.json`.
   - `sssf-agent-write` has no path under `${HOME}/.claude`, `${HOME}/.claude.json` or `${HOME}/.local/share/claude`.
   - The only allow rule naming `${HOME}/.local/share/claude` is `sssf-agent-read`, whose operations equal `agentsh_guard.READ`.
   - `sssf-agent-read` has no `${HOME}/github/skills/**`, `${HOME}/github/plugins/**`, `${HOME}/CLAUDE.md` or `${HOME}/AGENTS.md`.
   - `${HOME}/.config/sssf-sandbox/**` sits in `sssf-credentials` (deny, `*`), ahead of every allow.
2. `test_pi_policy_is_unchanged_by_the_claude_home_change`: pin the pi policy's `file_rules` section. Compare the rendered pi text with the same rendering from a copy of the baseline lists. The simplest form asserts that the pi `sssf-agent-write` paths equal `PI_WRITE`, that there is no `sssf-agent-read`, and that `sssf-other-sign-in` is `CLAUDE_SIGN_IN`. Plus test 4 of the existing `test_file_rules_equal_the_designs_deny_policy_output` for pi.
3. `test_claude_token_from_the_environment_then_the_file`:
   - With the environment variable set, the token comes from it.
   - With it unset and a temp file holding `CLAUDE_CODE_OAUTH_TOKEN="tok"`, it returns `tok`.
   - A missing file, or a file without the line, gives `""`.
4. `test_ensure_refuses_without_a_token_before_a_worktree_or_run_row`:
   - Use the existing `machine` fixture and the pattern of `test_ensure_refuses_a_missing_service_with_no_traceback` / `test_each_machine_refusal_fires_before_an_agent_starts` (read them first).
   - With no token and a roster from the shipped config, which has a Claude Code agent, `session.ensure` raises `SystemExit` whose text contains `claude setup-token` and `token.env`.
   - No worktree, no `sessions/<adw_id>` folder and no run row was made. Assert that the same way the neighbouring refusal test does.
5. `test_claude_code_child_env_under_agentsh`: a guard from `machine.guard.model_copy(update={"claude_config": str(tmp_path / "data" / "sessions" / "abc" / "claude-config")})` and a temp token file.
   - `agent_cc.child_env(guard=g, config_dir=g.claude_config)` has `CLAUDE_CONFIG_DIR == g.claude_config`, `CLAUDE_CODE_OAUTH_TOKEN` from the file, and `DISABLE_AUTOUPDATER == "1"`.
   - With the env var set, the env var wins.
   - With an engineer `CLAUDE_CONFIG_DIR=/home/u/.claude` in `os.environ`, the run's folder wins.
   - A text guard, or `guard=None`, gives no `CLAUDE_CONFIG_DIR` or `DISABLE_AUTOUPDATER`.
   - With no token, `child_env` under the agentsh guard raises `AgentshRefused` matching `claude setup-token`.
6. `test_a_claude_agent_without_a_token_starts_nothing`: drive `agent_cc.run` with the `fake`/`request` helpers already imported from `test_agent_cc`, under the agentsh guard on `sandbox.LOCAL`, with no token. It raises `AgentshRefused`, and the fake wrap's `FAKE_DUMP` file is never written.
7. `test_the_run_config_folder_is_made_real_before_launch`: with `claude_config` pre-planted as a symlink to a `tmp_path` folder standing in for `~/.claude`, a launch replaces the link with a real folder. The decoy target's contents are untouched. Use the fake wrap: the launch reaches `start`, and the dump shows `CLAUDE_CONFIG_DIR`.

`.claude/skills/sssf/tests/test_agent_cc.py`, beside the `auth` fixture tests:

8. `test_check_login_under_agentsh_uses_the_token_and_a_private_folder`: make the fake `claude` script (`FAKE_AUTH` or a variant) write its `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_OAUTH_TOKEN` and `DISABLE_AUTOUPDATER` to a temp file. Use a temp `TOKEN_FILE`.
   - The token and `DISABLE_AUTOUPDATER=1` reach the check.
   - `CLAUDE_CONFIG_DIR` is not the guard's `claude_config`, and it no longer exists after the call.
   - With no token, the check raises `NotLoggedIn` mentioning `claude setup-token`.
   - `check_login()` with no guard behaves as before: the existing two tests still pass unchanged.

Update existing tests only where they assert the old lists. Search the tests for `CLAUDE_WRITE`, `CLAUDE_READ`, `github/skills`, `.claude.json` and `local/share/claude`.

## Verification

This run is under agentsh, and the builder's own Claude Code still signs in the old way. The new code reaches the runner only after the merge.

1. As in runs 98e91ed7 and 47b4d0ea, copy `.claude` and `docs/designs/agentsh` to a fresh `/tmp` folder, keeping their relative paths so `DENY_POLICY` resolves. Then run:
   `uv run --no-config --directory <copy>/.claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py tests/test_agent_cc.py -q -p no:cacheprovider`
   Every new test, and `test_file_rules_equal_the_designs_deny_policy_output` for both harnesses, must pass.
2. Run the whole `tests` folder the same way. Wrap denials cause known failures (run 6e904be8: 172 failed, 12 errors). Compare the count, and report any new failure in `test_agentsh_guard.py` or `test_agent_cc.py`. The repo's own test check, run outside the wrap by SSSF's quality step, decides: `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.
3. In the copy, render the claude policy (`python -c` calling `agentsh_guard.policy_text` as in test 1) and `grep` it. There is no `${HOME}/.claude` or `${HOME}/.claude.json` in any allow rule, and `.local/share/claude` appears only under `sssf-agent-read`. The pi policy text is byte-identical to the one rendered at `30ffaf7`: render both with `git show 30ffaf7:<path>` into a temp folder, then `diff`.
4. No file under `app_docs/`, the repo-root `adws/` or `specs/` is touched.

After the merge (not the builder's job): the engineer reinstalls both repos' policies with `just agentsh-install`. The hub session reruns the probes under the wrap in the hub and in sssf-sandbox, with new decoy files only, and then a real chain in sssf-sandbox under `local_guard: agentsh`. Those probes cover the Bash and Read call, `--resume`, a subagent, the reads and writes the bar lists, and whether `~/CLAUDE.local.md` and the WSL path are still opened.

## Notes for the reviewer

- **The one deliberate departure from the task's wording**: the task says `check_login` runs with the run's `CLAUDE_CONFIG_DIR`. This plan gives `check_login` the same token and `DISABLE_AUTOUPDATER=1`, but a private temp config folder. The run's folder is writable by every agent of the repo (`sssf-sessions`). `claude auth status` runs outside the wrap without `--safe-mode`, so a planted `apiKeyHelper` in that folder's `settings.json` would be agent code run outside the wrap, which the bar forbids. The check still proves the token signs in.
- `session_files.own_dir` and the `tree is sandbox.LOCAL` condition are what make the per-run folder safe to hand over. They aren't extra guard changes beyond the six.
