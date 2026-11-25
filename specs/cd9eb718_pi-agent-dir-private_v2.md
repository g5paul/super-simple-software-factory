# Keep a pi agent from planting an extension in ~/.pi (third run, v2)

## Summary

Two runs planned this before. Run 2738b794 (`specs/2738b794_pi-own-agent-dir.md`) built it and was set aside for a bypass. Run 43f3b2bf (`specs/43f3b2bf_pi-agent-dir-private.md`, its v2) stopped at the spec. This plan is 43f3b2bf's v2, checked against `main` at `f28562b`, **minus `_blank_copy`**.

The design:

- Narrowing `PI_WRITE` isn't safe (see "What pi's source says"). So under `local_guard: agentsh` a wrapped pi agent gets an agent dir of its own, a host-issued sign-in token, and no writes under `~/.pi`.
- The agent dir is `~/.local/state/sssf-pi/<adw_id>`, outside the shared sessions tree. The Claude policy already refuses every read and write of that folder and of every folder above it (`sssf-default-deny`), so the Claude policy doesn't change.
- SSSF writes an empty `auth.json` and a copy of the engineer's `models.json` there before every call. The copy is not blanked afterwards: it is readable only by pi agents, which could read the original on the baseline.
- Every pi SSSF starts gets `--no-extensions`.
- A folder or link where SSSF writes a file ends the call with `SandboxFailed` naming the path, before the wrap starts.
- `config.md` says what `--no-extensions` leaves running, and every container known limit stays.

**Changed since v1 (`specs/cd9eb718_pi-agent-dir-private.md`), after spec review 1.** The design is the same. Two things changed:

1. The test for "Only what the spec names" compares each policy with the baseline's rendered text, pasted into the test as a fixed string. v1 built the expected rules from the module's own constants, so an edit to `HOME_READ` or `MKDIR_PARENTS` would have passed.
2. The widening now names the agent dir's `skills/` and context files (`AGENTS.md`, `CLAUDE.md`), in the Threat model, Out of scope, `docs/open.md` and `config.md`. v1 said the global copies could no longer be written. That was wrong: the global copies a wrapped pi loads are now the ones in its own agent dir, which any run's pi agent can write.

Also new: the Verification section says what the planner saw when it ran the test command, and the engineer's proof names providers an extension registers.

The builder may read 2738b794's code with `git show sssf/2738b794:<path>` or `git diff f28562b sssf/2738b794 -- <path>` and reuse it where this plan says "as 2738b794". Do not merge or cherry-pick that branch.

## What the planner checked against `main`

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` and `docs/designs/agentsh/deny-policy.py` are the same at `2e91738` (43f3b2bf's baseline) and `f28562b`. So 43f3b2bf's reading of the policy holds.
- `file_rules`: the pi entry is `(CLAUDE_SIGN_IN, [], [], PI_WRITE)`, `sssf-agent-write` is always emitted, and `sssf-agent-read` only when `reads` isn't empty. `MKDIR_PARENTS` holds `${HOME}/.local/state` and `${HOME}/.local`. `HOME_READ` holds `${HOME}/.local` and nothing under `.local/state`. `CLAUDE_WRITE` holds `.local/state/claude/**` only. No Claude allow rule matches `~/.local/state/sssf-pi` or anything in it.
- `session_files.write(path, data, root)` walks from `root` without following links, replaces a link or file at a folder's place, and writes by temp file and `os.replace`. A folder at the target makes `os.replace` raise `IsADirectoryError`, an `OSError`; the temp file is removed. `root` itself is opened without `O_NOFOLLOW`, so the caller must check it.
- `agentsh_guard.start` makes the agent cache with `os.makedirs` of the parent, `os.mkdir(cache, 0o700)` and `os.lstat`. `_pi_homes` copies that.
- `tests/test_rules.py::test_the_refuse_extension_loads_after_every_user_extension` reads the `-e` values from argv, so new flags don't break it.
- pi's source, read again from the installed `@earendil-works/pi-coding-agent` (1.4.0): `resource-loader.js:369`, `:409`, `:497`, `:505`; `package-manager.js:1448`; `resolve-config-value.js:66`; `config.js:504` and `:519`. They say what 43f3b2bf's spec says. The rest of the table below is taken from that spec and 2738b794's.
- Places SSSF starts pi: the same list as 43f3b2bf's (see change 3).

## What pi's source says (pi 1.4.0)

No pi was run and nothing under `~/.pi` was written, so "what a wrapped pi writes" comes from source, not a trace.

| Path under the agent dir | pi writes it when | pi loads from it | Source |
| --- | --- | --- | --- |
| `auth.json` | a token refresh, a login | **credentials; a `key` starting with `!` is run as a shell command** | `core/auth-storage.js`, `core/resolve-config-value.js` |
| `auth.json.lock/` (a folder) | every read or write of `auth.json` | nothing | `core/auth-storage.js` |
| `settings.json`, `settings.json.lock/` | a setting changes | config: `packages`, `npmCommand`, extensions | `core/settings-manager.js:795`, `core/settings-schema.js:181` |
| `models-store.json` (+ lock) | a provider catalog refresh | the model catalog | `core/models-store.js`, `core/model-runtime.js` |
| `models.json` | never | custom providers; `apiKey` and header values are literals unless they start with `!` or name a `$VAR` | `core/model-runtime.js`, `core/resolve-config-value.js` |
| `bin/fd`, `bin/rg` | the `find` or `grep` tool, when the binary is in neither `bin/` nor `PATH` | **executed, ahead of `PATH`** | `utils/tools-manager.js` |
| `extensions/`, `themes/`, `prompts/`, `trust.json` | not in `-p` mode | code and config | `config.js`, `core/trust-manager.js` |
| `skills/`, `AGENTS.md`, `CLAUDE.md` | not in `-p` mode | text put in front of the model: global skills and context files | `config.js`, `core/resource-loader.js` |
| `sessions/` | never with `--session-dir` | nothing | `config.js` |

- **Why narrowing fails.** `auth.json` must be writable for a token refresh, and a `!command` key written there runs in the next unwrapped pi (the engineer's, and SSSF's `pi auth print-bearer-token`). `bin/` has the same shape. A file pi must write is also one it loads commands from, so wrapped agents move off `~/.pi` altogether.
- **pi's `models.json` path is fixed to the agent dir** (`config.js:504`), so a wrapped pi with its own agent dir needs a copy to keep custom providers.
- **`pi auth` loads no extensions.** `main.js:460` runs the auth command and returns before resources load.
- **What `--no-extensions` keeps.** `resource-loader.js:409`: with `noExtensions` the extension list is the `-e` paths only. Every `-e` path SSSF passes still loads, a roster's `extensions` and the refuse extension included. Built-in tools are not extensions, and `--tools` is unaffected.
- **What `--no-extensions` does not stop.** `resource-loader.js:369` and `:497` call `packageManager.resolve()` whatever the flag says. For a `settings.json` package that isn't installed, `package-manager.js:1014-1019` installs it through `getNpmCommand()` (`:1448`), which reads `settings.json`'s `npmCommand`. So a `settings.json` in the agent dir with `packages` and `npmCommand` runs a command at every pi start. Only `PI_OFFLINE`/`--offline` stops the install; see For the engineer.
- **Skills and context files have one switch each.** `--no-skills` and `--no-context-files` turn off the project's and the agent dir's together. pi has no flag for the agent dir's alone.
- **Where a token goes.** `pi-ai`'s `env-api-keys.js`: `openai` reads `OPENAI_API_KEY`; `anthropic` reads `ANTHROPIC_OAUTH_TOKEN` ahead of `ANTHROPIC_API_KEY`.

## Threat model

- A pi agent under `local_guard: agentsh` runs as the engineer's user and writes wherever the pi policy allows. On the baseline that is all of `~/.pi`.
- pi starts outside the wrap: the engineer's own pi, `agent_pi._pi_catalog` (`pi --list-models`), `agent_pi.vm_token`, `trial.py` (`pi auth check`) and `container/pi_token_broker.py`. The first three get `.env`'s keys.
- Every agent of every run, on either coding agent, may read, write **and rename** the whole sessions tree (`sssf-sessions`), and in a plain run everything in `${PROJECT_ROOT}`. A deny on one path inside those trees doesn't hold: an agent renames a folder above it and reads the file at its new path. A file that must stay from Claude Code agents can't live where a Claude agent may rename a parent folder.
- The Claude policy allows nothing at `~/.local/state/sssf-pi`, nor a write, rename, link or delete of any folder above it (`~/.local/state`, `~/.local`, `~`, `/home`, `/`). `sssf-mkdir-parents` allows `mkdir` on some, the read rules allow looking at some, and the rest falls to `sssf-default-deny`. This holds for an agent of any run, during another run's call.
- **The widening.** The pi policy's new allow, `${HOME}/.local/state/sssf-pi/**`, has no `<adw_id>` in it: a policy is installed per repo, not per run. So a wrapped pi agent can write its own run's agent dir and any other run's, in any repo on the machine. What it can leave there:
  - **Commands:** `settings.json` with `packages` and `npmCommand`, and `bin/fd` or `bin/rg`. They run in later wrapped pi agents.
  - **Text for the model:** `skills/`, `AGENTS.md` and `CLAUDE.md`. Agents keep skills and context files on, so a later wrapped pi agent loads them as its global skills and context. That is a prompt injection from one pi agent into another, across runs and repos.
  - **Config:** `models-store.json`.

  All of it is read and run only by wrapped pi agents, inside the wrap, with an agent's environment. No unwrapped pi and no Claude Code agent reads that folder. On the baseline the same files under `~/.pi/agent` were writable too, and there the engineer's own pi read them as well.
- The guard can't stop what agentsh doesn't trace (xattr), does nothing under `local_guard: none`, and can't keep an agent that may read a file (a pi agent, the copy) from copying it to a place another agent reads.
- The rules are judged on a path after agentsh resolves links, as every existing deny assumes. This plan leans on nothing more than that and default-deny.

## Baseline

`main` at `f28562b`. Every finding is judged against it. There:

- `PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]`, with every operation, in the pi policy's `sssf-agent-write`.
- `_pi_catalog` runs `[PI_PATH, "--list-models"]`.
- An agent's pi command has no discovery flag.
- SSSF writes nothing for a pi agent but its session files and raw output.
- A Claude Code agent can't read `~/.pi/agent/models.json` (`sssf-default-deny`) or `auth.json` (`sssf-other-sign-in`). A pi agent can read and write both.

## Bar

Against the baseline, under `local_guard: agentsh`:

- **No agent file kept under `~/.pi`.** No rule in the pi policy allows a write, create, rename, link or mkdir there.
- **No agent code run on the host through pi.** `_pi_catalog` and every agent's pi carry `--no-extensions`.
- **No engineer file deleted or overwritten.** SSSF writes only under `~/.local/state/sssf-pi`, a folder it makes itself and no agent can replace, through `session_files.write`, which never follows a link below that folder.
- **No engineer file reaches an agent that couldn't read it on the baseline.** Under the Claude policy, in a plain run and a worktree run, every read of the `models.json` copy, of `auth.json` and of the agent dir decides `deny`. So does a rename, link, symlink, delete or write of the agent dir and of every folder above it. Nothing SSSF writes for a pi agent under the data root holds the copy.
- **A broken agent dir stops cleanly.** A folder, or anything else SSSF's write can't replace, at the agent dir's `auth.json` or `models.json` raises `SandboxFailed` naming the path, and the wrap is never started.
- **The sign-in**, by the call's provider, as 2738b794:
  - `openai` or `anthropic` get a token from the host's `pi auth print-bearer-token`. With no host credential and no key in the environment, `SandboxFailed` names the provider.
  - Another provider whose host credential is a stored sign-in (`authType` `oauth`) stops with `SandboxFailed`. Another provider with a key uses the environment's.
  - `SSSF_PI_AUTH=subscription` still keeps API keys from the agent.
  - The token is never printed and never written to a file.
- **The Claude policy is unchanged.** For each shipped roster its rendered text equals the baseline's, byte for byte. In the pi policy only `sssf-agent-write`'s paths change and `sssf-agent-read` is added. The test's expected values are fixed text from `f28562b`, not values built from the module.
- Every existing test passes on the host, apart from the ones this plan rewrites by name.
- Never: a `config.md` sentence that says `--no-extensions` stops a `settings.json` package. Never: a `_blank_copy` or any code that rewrites the copy after a call.

## Who can read each file SSSF writes for a pi agent

`<dir>` is `~/.local/state/sssf-pi/<adw_id>`.

| File | What's in it | Claude Code agent (any run, plain or worktree) | pi agent (any run, any repo) | Against the baseline |
| --- | --- | --- | --- | --- |
| `<dir>/models.json` | a copy of the engineer's `models.json`, which may hold literal keys; kept between calls | **denied**, `sssf-default-deny`; no rename of `<dir>` or a folder above it is allowed either | allowed (`sssf-agent-write`) | The same: a pi agent reads the original through `PI_WRITE`; a Claude agent can't |
| `<dir>/auth.json` | `{}` | denied, the same way | allowed | Tighter: on the baseline a pi agent reads the real `auth.json` |
| pi's session files (`--session-dir`) and `raw_output.jsonl`, in the sessions tree | the agent's own conversation | allowed | allowed | Unchanged; not touched by this plan |
| the sign-in token | environment only | no (`sssf-proc-secrets` denies `/proc/*/environ`) | the agent's own | New; never in a file |

**Another run's pi agent is not denied.** On the baseline every pi agent reads `~/.pi/agent/models.json`, so the copy tells it nothing new. A test pins that this read is allowed, so the choice is on record.

**Why this folder.** Run 43f3b2bf's v1 kept the copy in the sessions tree behind a Claude deny, and its review found the rename route in the Threat model. Denying a rename of every folder above the copy needs a complete list for every `data_dir`, and in a plain run it includes `${PROJECT_ROOT}` itself. A folder under `~/.local/state` depends only on default-deny: the Claude policy has no allow that matches it or a parent, and doesn't change at all. The cost is one changed allow in the pi policy and a folder in the engineer's home that SSSF makes.

**Worktree runs.** The folder's rules use `${HOME}` only, never `${PROJECT_ROOT}` or the sessions path. The same rules decide for a plain run and a worktree run, in every repo.

**Why the copy isn't blanked.** Review 2 of 43f3b2bf: blanking keeps the copy from nobody who couldn't read the original on `main`. Claude agents are denied the folder, pi agents could read the original, and containers don't mount it. SSSF rewrites the copy before every call, so it never goes stale.

## Changes

### 1. `.claude/skills/sssf/tests/test_agentsh_guard.py`, the baseline text, first

Do this **before** touching `agentsh_guard.py`, while `git diff f28562b -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` is empty.

- Render the baseline policies from the untouched module with fixed inputs: `agentsh_guard.policy_text(repo_settings.RepoSettings(), "/home/u/github/repo/.git", harness, "n", sessions="/home/u/github/repo/adws/adw_data/sessions", refuse_script="/home/u/github/repo/adws/adw_modules/sssf_refuse.py")`, for `harness` `claude_code` and `pi`.
- Paste the two outputs into the test file as module-level strings, `BASELINE_CLAUDE` and `BASELINE_PI`, whole: `command_rules`, `file_rules`, `network_rules` and `signal_rules`. A comment says they are `policy_text`'s output at `f28562b` with those inputs, and that they are never to be rebuilt from the module.
- These strings are the only expected values the "Only what the spec names" test uses. See change 5.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

- Add `PI_HOMES = ".local/state/sssf-pi"`, with a comment: the pi agent dirs of wrapped agents, one per run; under the home folder because no Claude Code agent may read it or write, rename or link any folder above it; SSSF makes it, and no policy lets an agent replace the folder itself.
- `PI_WRITE` becomes `[f"${{HOME}}/{PI_HOMES}/**"]`: what is inside the folder, not the folder. Nothing in `~/.pi`.
- Add `PI_READ = ["${HOME}/.pi/agent/bin", "${HOME}/.pi/agent/bin/**", f"${{HOME}}/{PI_HOMES}"]`, with a comment: pi's managed `fd` and `rg`, read and run by agents and never written, and the homes folder itself, which pi may stat on its way in. Everything else in `~/.pi` falls to `sssf-default-deny`.
- Add `def pi_home(adw_id: str, home: Optional[str] = None) -> str`, returning `os.path.join(home or str(Path.home()), PI_HOMES, adw_id)`. `agent_pi` and the tests both call it, so the path a test judges is the path SSSF writes.
- In `file_rules`, the `"pi"` entry becomes `(CLAUDE_SIGN_IN, [], PI_READ, PI_WRITE)`. Nothing else in the function changes. `sssf-agent-read` now appears for pi because `reads` is no longer empty.
- Update the comment above `CLAUDE_WRITE`/`PI_WRITE` and the module docstring where they describe pi's writes.

### 3. `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` compares this script's output with `file_rules`. Mirror the new `PI_WRITE`, `PI_READ` and the `"pi"` entry (its lines 110 and 140).

### 4. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

As 2738b794 unless marked **new**.

- **Host call.** `HOST_FLAGS = ("--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files")`. `_pi_catalog` runs `[PI_PATH, "--list-models", *HOST_FLAGS]`. `env` stays `_in_container(operator_env(), sandbox.LOCAL)`. A model list needs none of the five.
- **Other host starts.** No argv changes:
  - `vm_token`, `templates/adws/adw_modules/trial.py`'s `pi auth check` and `templates/container/pi_token_broker.py` run `pi auth`, which returns before resources load (`main.js:460`). Add a one-line comment at `vm_token`.
  - `kill.py` imports `PI_PATH` to match processes and starts nothing.
  - `templates/harness_engineering/subagents.ts` starts pi inside an agent, already with `--no-extensions`.
  - `scripts/install.py` only prints a `pi auth check` line. `scripts/e2b_template.py`, `sandbox_openshell.py` and `sandbox_remote.py` run `pi --version` inside an image or VM.
- **Agent call.** `AGENT_FLAGS = ("--no-extensions", "--no-prompt-templates", "--no-themes")`, added to `cmd` after `--system-prompt` and before `--tools`, on every tree and under every `local_guard`. The `-e` loop and the refuse extension last are untouched. In a VM the refuse extension is passed with `-e` too, so it still loads.
  - **Skills and context files stay on.** The project's `AGENTS.md`, `CLAUDE.md` and skills are the text a repo gives its agents, the project's copies are in `protected_files`, and pi has no flag that turns off the agent dir's copies alone. The cost: under agentsh the agent dir's `skills/`, `AGENTS.md` and `CLAUDE.md` are writable by any run's pi agent, and later wrapped pi agents load them. That is in the Threat model's widening and in Out of scope. The comment at `AGENT_FLAGS` says this, not that the global copies can't be written.
  - Prompt templates and themes are off because a `-p --mode json` call uses neither.
- **The host token.** `_host_token(provider, check=False)`, `SIGN_IN`, `API_KEY_VAR` and `_sign_in(env, provider)` as 2738b794. `vm_token` calls `_host_token("openai")` and keeps its messages.
- **The agent's own dir.** `_own_agent_dir(env, tree, request, provider)`, called in `run` after `_in_vm` and before `_in_container`. It returns `env` unchanged unless `tree is sandbox.LOCAL` and `request.local_guard` is an agentsh guard (`guard is not None and guard.mode == "agentsh"`). Otherwise, in this order (**new**: the sign-in comes first, and the folder is not in the sessions tree):
  1. Unless `SSSF_PI_TOKEN_SOCKET` is set, `_sign_in(env, provider)`.
  2. **new:** If `request.adw_id` isn't one plain path part (`re.fullmatch(r"[A-Za-z0-9_-]+", ...)`), raise `SandboxFailed` naming it. Then `homes = _pi_homes()` and `agent_dir = agentsh_guard.pi_home(request.adw_id)`.
  3. `_write_own(Path(agent_dir) / "auth.json", "{}", homes)`.
  4. Read the host's `models.json` (`Path(MODELS_JSON)`) with `_read_regular`. A missing file gives `b"{}"`; any other `OSError` raises `SandboxFailed` naming `MODELS_JSON`. Then `_write_own(Path(agent_dir) / "models.json", <bytes>, homes)`. The bytes are not parsed.
  5. `env["PI_CODING_AGENT_DIR"] = agent_dir`, and append `str(Path(MODELS_JSON).parent / "bin")` to `env["PATH"]`, last.
- **new: `_pi_homes() -> str`.** The folder `Path.home() / agentsh_guard.PI_HOMES`, made the way `agentsh_guard.start` makes the agent cache: `os.makedirs` of its parent with `exist_ok`, `os.mkdir(homes, 0o700)` ignoring `FileExistsError`, then `os.lstat`. If it isn't a real folder, or any step raises `OSError`, raise `SandboxFailed` naming the path and saying to look at it with `ls -la`. It is the `root` every write below it is given, so `session_files` opens each folder under it without following a link, and a link an agent left at `<homes>/<adw_id>` is replaced, not followed.
- **new: `_write_own(path, data, root)`.** Calls `session_files.write` and turns any `OSError` (`IsADirectoryError`, `session_files.LinkedPath`, a non-empty folder) into `sandbox.SandboxFailed`, raised `from None`. The message has the path, says SSSF can't write it because something that isn't a file is there, that an agent may have put it there, and to look at it with `ls -la`, remove it and start again.
- **Not built:** `_blank_copy`, and any `try/except BaseException` around `_in_container` and `agentsh_guard.start`. `run`'s existing `try`/`finally` is untouched.
- Docstrings: the module docstring, `_pi_catalog`, `vm_token`, `run`, and the new functions. `run`'s says that under an agentsh guard the agent has its own agent dir in `~/.local/state/sssf-pi` and a host-issued sign-in, reads nothing in `~/.pi` but `bin/`, and that SSSF rewrites `auth.json` and the `models.json` copy before every call.

### 5. `.claude/skills/sssf/tests/test_agentsh_guard.py`, the rest

All with `shipped_text` and `Rules`, as the file's other policy tests. `main` is each of `MAINS`; the two roots are `main` and `WORKSPACE`.

- **The decoy.** Under the pi policy:
  - For each op in `WRITE_OPS`, `${HOME}/.pi/agent/extensions/x.js`, `${HOME}/.pi/agent/settings.json`, `${HOME}/.pi/agent/auth.json`, `${HOME}/.pi/agent/bin/rg` and `${HOME}/.pi/sssf-decoy/agent/extensions/x.js` decide `deny`, and no rule whose decision is `allow` matches.
  - With `d = agentsh_guard.pi_home("abc", HOME)`: `d/auth.json`, `d/auth.json.lock`, `d/models.json`, `d/models-store.json` and `d/bin/fd` match `sssf-agent-write` for `write`, `create`, `mkdir`, `rmdir` and `rename`.
  - A read of `${HOME}/.pi/agent/bin/rg` is `sssf-agent-read`. A read of `${HOME}/.pi/agent/auth.json` and of `${HOME}/.pi/agent/models.json` is denied.
  - The homes folder itself (`${HOME}/.local/state/sssf-pi`) can be read, but each op in `WRITE_OPS` on it is denied, `mkdir` included.
- **The copy stays private.** Under the Claude policy and both roots: for `d`, `d/models.json`, `d/auth.json` and `d/bin/rg`, each of `read`, `open`, `stat`, `list` and each op in `WRITE_OPS` decides `deny`.
- **No folder above it moves.** Under the Claude policy and both roots, for each of `d`, `${HOME}/.local/state/sssf-pi`, `${HOME}/.local/state`, `${HOME}/.local` and `${HOME}`: `rename`, `link`, `symlink`, `delete`, `rmdir`, `write`, `create`, `chmod` and `chown` decide `deny`. `mkdir` is left out: `sssf-mkdir-parents` allows it on existing parents, and it moves nothing.
- **Why the folder moved.** Under the Claude policy and both roots, a rename of `<main>/adws/adw_data/sessions/abc` and of `<main>/adws/adw_data/sessions` is allowed, and a read of `<main>/adws/adw_data/sessions/abc/pi-agent/models.json` is allowed. A comment says this is 2738b794's path and the rename route.
- **Another run's pi agent.** Under the pi policy, a read of `agentsh_guard.pi_home("other", HOME) + "/models.json"` is allowed, with a comment naming the who-reads table.
- **Only what the spec names.** For each shipped roster under `.claude/skills/sssf/templates/` (`sssf.config.yaml`, `sssf.config.chatgpt.yaml`, `sssf.config.claude_code.yaml`), load it and take the harnesses its agents use, as `guard_for` does (`claude_code` or `pi` by each agent's `coding_agent`). For each harness, render `policy_text` with change 1's fixed inputs.
  - A `claude_code` policy equals `BASELINE_CLAUDE`, as text, byte for byte.
  - For a `pi` policy, parse it and `BASELINE_PI` with `yaml.safe_load`. `command_rules`, `network_rules` and `signal_rules` are equal. In `file_rules`, the test removes `sssf-agent-read` from the new list and sets `sssf-agent-write`'s paths to the baseline's; the two lists are then equal, in order. The test also asserts that the removed rule sat right after `sssf-agent-write`, and that its paths are `PI_READ` and its operations `READ`.
  - Every roster's harness set is covered, and the test fails if a roster has a harness with no baseline string.
  - No expected value comes from an `agentsh_guard` constant, apart from that last `PI_READ` check. The test never calls git.
- Rewrite `test_pi_policy_keeps_its_shape` for the new `sssf-agent-write` paths and the new `sssf-agent-read` (`PI_READ`, operations `READ`); `sssf-other-sign-in` unchanged.
- In `test_the_file_rule_order_matches_main_around_the_new_allows`, `sssf-agent-read` moves out of the Claude-only set into the set both policies have. Change any other test line that says the pi policy has no `sssf-agent-read` or that `~/.pi` is writable, and nothing more.

### 6. `.claude/skills/sssf/tests/test_agent_pi.py`

Every new test sets `HOME` to a folder under `tmp_path` with `monkeypatch.setenv`, and `agent_pi.MODELS_JSON` to a dummy file under `tmp_path`, so nothing is read or written in the real home. The guard is `LocalGuard(mode="agentsh", ...)` with `agentsh_guard.start` stubbed.

- **The host call.** A fake `pi` script records its argv and environment. `_pi_catalog()` (cache cleared before and after) passes `--list-models` and every `HOST_FLAGS` entry, and the environment it got equals `operator_env()`.
- **The agent call.** With `_fake_pi` and an extension `a.ts`: argv holds each `AGENT_FLAGS` entry, `-e a.ts`, and the refuse extension as the last `-e`. `tests/test_rules.py::test_the_refuse_extension_loads_after_every_user_extension` passes unchanged.
- **The own dir.**
  - The child's `PI_CODING_AGENT_DIR` equals `agentsh_guard.pi_home(<adw_id>)` and is not under `request.data_root`. The homes folder is mode `0700`.
  - When the stub is called, `auth.json` there is `{}`, even when one with an `openai` entry was there before. `models.json` equals the dummy file, which holds a literal `apiKey`. `PATH` ends with the dummy file's sibling `bin`.
  - No file under `data_root` holds the key.
  - A second call rewrites both files: change the dummy and the copy follows.
  - A link at `<homes>/<adw_id>` pointing outside is replaced, not followed, and its target is untouched.
  - A link or a file at the homes folder itself raises `SandboxFailed` naming it, and the stub is never called.
  - An `adw_id` of `..` or `a/b` raises `SandboxFailed`, and nothing is written.
  - With `local_guard` `none`: no `PI_CODING_AGENT_DIR`, no `pi auth` call and no homes folder.
- **A folder where a file goes.** With a folder at the agent dir's `auth.json`, and again at `models.json` (each also with a file inside it): the call raises `sandbox.SandboxFailed` whose message holds the path, the exception is not an `IsADirectoryError`, the `start` stub was never called, and the folder is still there.
- **The sign-in**, each with a fake `pi` that answers `auth`, as 2738b794's tests:
  - `openai`: the token is in `OPENAI_API_KEY`.
  - `anthropic`: a token goes in `ANTHROPIC_OAUTH_TOKEN`, an `sk-ant-api` value in `ANTHROPIC_API_KEY`.
  - A failed `pi auth` with no key raises `SandboxFailed` naming the provider, with the wrap never started and no `models.json` written. With a key it goes on.
  - An API key under `SSSF_PI_AUTH=subscription` raises without the key in the message.
  - `google` with `oauth` raises; with `api_key` or a failure it goes on.
  - `SSSF_PI_TOKEN_SOCKET` set makes no `pi auth` call.
- No test for blanking.

### 7. Docs

- `docs/open.md`: delete the row "A pi agent under agentsh can plant code that runs unwrapped on the host the next time anything starts pi". Add one row for what's left: a wrapped pi agent can change any run's agent dir under `~/.local/state/sssf-pi`, in any repo. It names the commands (`settings.json` with `packages` and `npmCommand`, `bin/`), the text for the model (`skills/`, `AGENTS.md`, `CLAUDE.md`), `models-store.json`, and `auth.json` or `models.json` between SSSF's write and pi's read. Wrapped pi agents then read and run them, inside the wrap only. Keep the rows on the starter harness's pi subagent and on a project's own `.pi/settings.json`.
- `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh" (line 822): a "pi agents under agentsh" list beside the Claude Code one (lines 835-840):
  - They don't use `~/.pi/agent`. SSSF gives each run `~/.local/state/sssf-pi/<adw_id>`, with an empty `auth.json` and a copy of your `models.json`, both written before every call. The copy stays there between calls. Claude Code agents can't read the folder or move a folder above it. pi agents of any run can read and write it, as they could read your `models.json` before. You can delete the folder between runs.
  - They can write nothing in `~/.pi` and read only `~/.pi/agent/bin`. Your `settings.json`, global skills and global `AGENTS.md` don't reach them.
  - The sign-in rules from the Bar, the 35-minute token, and that a key for any other provider goes in `.env`.
  - SSSF starts pi with discovery off: `--no-extensions` everywhere, the full list for `--list-models`, what stays on for agents and why.
  - **What `--no-extensions` leaves running:** pi still resolves the `packages` in `settings.json` and installs a missing one with `npmCommand`. For the host's `pi --list-models` that is your own `~/.pi/agent/settings.json`, which agents can no longer write, and a trusted project's `.pi/settings.json` (`docs/open.md`). For a wrapped agent it is the `settings.json` in its agent dir, which pi agents can write, so a command there runs in later wrapped agents, inside the wrap. The same goes for the agent dir's `bin`.
  - **What a pi agent can leave for the next one:** skills and context files stay on, so a `skills/` folder, `AGENTS.md` or `CLAUDE.md` in an agent dir is loaded by later wrapped pi agents. Any run's pi agent can write them.
  - If something that isn't a file sits at the agent dir's `auth.json` or `models.json`, the call stops and names it.
  - A "Reinstall" line: the pi policy changed, so rerun `just agentsh-install`.
- `config.md`, the subscription sign-in bullet (line 108): under agentsh the sign-in reaches a pi agent as a host-issued token, for `openai` and `anthropic`.
- `config.md`, "pi in the operator container" (lines 985-1011). **Every known limit stays.** Edits:
  - Line 991 (code in `extensions/`, or a `settings.json` package): cut to what pi's source still allows. SSSF passes `--no-extensions`, so a file in the agent dir's `extensions/` no longer loads (`resource-loader.js:409`). A `settings.json` there with `packages` and `npmCommand` still runs a command at every pi start (`resource-loader.js:369`, `package-manager.js:1014`), after `check_stored_login`, and that command can plant an openai login. Still a known limit.
  - Line 989 (other sign-ins not checked) and line 999 (a swap between check and read): add that with `local_guard: agentsh` the files checked are the ones in the run's own agent dir, which pi agents can write.
  - Line 993: add "or SSSF's copy of it".
  - Line 985: with `local_guard: agentsh`, "the `auth.json` pi reads" is the one in the run's own agent dir.
- For the documenter: any `app_docs/` write-up that says a pi agent may write `~/.pi` describes the old behaviour. The repo keeps no CHANGELOG.

## Verification

The planner and builder run under agentsh, so run the two files only, from the repo root, the way the repo's own test command does:

`uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py tests/test_agent_pi.py -m 'not pushes and not host_only' -q -p no:cacheprovider`

**What the planner saw on the untouched baseline.** Neither of its two runs exited 0, though every test in both files ran with no failure and one skip:

- As written: exit 1. After the last test, pytest's end-of-session cleanup raised `RecursionError` in `shutil.rmtree` on stale `/tmp/pytest-of-gpaul/garbage-*` folders left by earlier runs. No summary line is printed.
- With `--basetemp "$(mktemp -d)/t"` added: killed with exit 137 after the last test. The cause wasn't found; it may have been the planner's own 10-minute limit.

Run 43f3b2bf's planner reported 445 passed, 1 skipped, in about 220 s, on `2e91738`, where both files and the modules they test are the same. So the command is not in `checks`: it would fail every build on this machine for a reason no builder can fix. The builder judges its runs by the progress lines (no `F` or `E`) and says in its report what the exit status was. It does not change code or tests to make the cleanup pass, and leaves `/tmp/pytest-of-gpaul` alone. The whole suite is left to verify, on the host.

Don't run a real pi against `~/.pi/agent`, and don't write anything under `~/.pi` or the real `~/.local/state`; the tests use a fake `pi`, `tmp_path` and a `HOME` under it.

Check by hand before handing in:

- `git diff f28562b -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows only `PI_HOMES`, `PI_WRITE`, `PI_READ`, `pi_home`, the `"pi"` entry and comments. No rule is added to or removed from the Claude policy.
- `BASELINE_CLAUDE` and `BASELINE_PI` were rendered before that file was edited. To recheck: `git show f28562b:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` has the old `PI_WRITE`, and `BASELINE_PI`'s `sssf-agent-write` lists `${HOME}/.pi` and `${HOME}/.pi/**`.
- No sentence in `config.md` says a `settings.json` package doesn't load or run, and none says the copy is blanked.
- `agent_pi.py` has no `_blank_copy`.

## Out of scope

Holes this change leaves open:

- The xattr gap, which is agentsh's.
- pi's trust model for a project's `.pi/` folder, past the flags above, and a trusted project's `.pi/settings.json` running a command in the host's `pi --list-models` (already in `docs/open.md`).
- The Claude policy's writes.
- VM runs, where `~/.pi` is the VM's.
- Runs with `local_guard: none`: pi agents use `~/.pi/agent` as before. They do get `AGENT_FLAGS`.
- **The widening.** One wrapped pi agent changing an agent dir for a later wrapped pi agent, of any run in any repo on the machine. Only wrapped pi agents read what lands there:
  - commands: `settings.json` (`packages`, `npmCommand`) and `bin/`;
  - text for the model: `skills/`, `AGENTS.md` and `CLAUDE.md`, which agents load because skills and context files stay on;
  - `models-store.json`, and `auth.json` or `models.json` between SSSF's write and pi's read.
- The copy keeps the engineer's `models.json` between calls, literal keys included. It is readable only by pi agents, as the original was on the baseline.
- A pi agent copying what it reads from the copy to a place a Claude Code agent can read. On the baseline it can do the same with the original.
- A checkout or `data_dir` placed under `~/.local/state`, where the Claude policy's workspace or config-folder rules would reach the folder.
- The 35-minute token against a call's 30-minute limit, the same margin a VM run has.
- A key for a provider other than `openai` and `anthropic` that is stored in `auth.json` and not in `.env`: the wrapped pi finds no credential.
- The engineer's own global skills, `AGENTS.md` and `settings.json` in `~/.pi/agent` no longer reach a wrapped pi agent.
- Every container known limit in `config.md`, all kept.
- A stale `auth.json.lock` an agent leaves in an agent dir, and old runs' folders under `~/.local/state/sssf-pi`, which nothing removes.

## For the engineer

- `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agent_pi.py`, the hub's own install, are protected and stay as they are until you update the install after the merge.
- The pi policy changes and the Claude policy doesn't. Rerun `agentsh_install.py` for the hub and for pi-agent-observability.
- Repeat the decoy under the new pi policy, then run one pi-roster call. That call is the first real proof of what this plan takes from source only:
  - pi on the host accepts the ChatGPT token as `OPENAI_API_KEY` with an empty `auth.json`;
  - `--no-extensions` breaks no roster. One thing it changes: a provider that only an extension in `~/.pi/agent/extensions/` registers no longer appears in `pi --list-models`, so a roster model from such a provider no longer resolves;
  - `find` reaches `fd` through `PATH`;
  - pi starts in an agent dir whose parent it may read but not write. If pi tries to `mkdir` the parent and stops on the refusal, the fix is `PI_HOMES` in `MKDIR_PARENTS`, which changes the Claude policy too and so needs its own run;
  - try one `anthropic/*` pi call if you use that sign-in.
- The two-file test command ends with a pytest cleanup error on this machine (see Verification). Clearing `/tmp/pytest-of-gpaul/garbage-*` is yours to do.
- Not done here, for you to weigh: `--offline` (or `PI_OFFLINE=1`) stops pi installing a missing `settings.json` package (`package-manager.js:1015`), which would close the `npmCommand` route. It also puts pi's model runtime offline (`core/model-runtime.js:97`), and whether `--list-models` and a wrapped call still work that way needs a real pi run, which this run may not make.
