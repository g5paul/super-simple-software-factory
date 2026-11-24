# Keep a pi agent from planting an extension in ~/.pi (second run, v2)

## Summary

Run 2738b794 planned and built this once (`specs/2738b794_pi-own-agent-dir.md`). Its reading of pi 1.4.0 and its design hold against `main`: narrowing `PI_WRITE` isn't safe, so a wrapped pi agent gets an agent dir of its own, a host-issued sign-in token, and no writes under `~/.pi`, and every pi SSSF starts gets `--no-extensions`. This plan rebuilds that design and fixes the three things its bypass review found:

1. **The `models.json` copy was readable by Claude Code agents.** The agent dir moves out of the shared sessions tree to `~/.local/state/sssf-pi/<adw_id>`, where the Claude policy's `sssf-default-deny` already refuses every read and every write, of the folder and of each folder above it. SSSF also blanks the copy when the call ends.
2. **`config.md` said `--no-extensions` stops a `settings.json` package.** It doesn't. The docs now say what still runs, and the container's known limit stays, cut to the half that is still true.
3. **A folder where a file goes raised `IsADirectoryError`.** SSSF's writes into the agent dir now end the call with `SandboxFailed` naming the path, before the wrap starts.

**Changed since v1 (`specs/43f3b2bf_pi-agent-dir-private.md`), after spec review 1.** v1 kept the agent dir at `sessions/<adw_id>/pi-agent` and added a Claude deny rule, `sssf-pi-home`, on that path. The review found that a Claude Code agent could rename a folder above it (`sessions/<adw_id>`, `sessions`, `adws/adw_data`, `adws`) into the workspace and read the copy at a path the rule doesn't match. The review offered (a) a second deny on every such folder, or (b) a folder with no ancestor a Claude agent can write. This plan takes **(b)**; "Why this folder" below says why. `sssf-pi-home` is gone and the Claude policy is the baseline's, byte for byte.

The builder may read 2738b794's code with `git show sssf/2738b794:<path>` or `git diff 2e91738 sssf/2738b794 -- <path>` and reuse it where this plan says "as 2738b794". Do not merge or cherry-pick that branch.

## What pi's source says (pi 1.4.0)

Read from the installed `@earendil-works/pi-coding-agent/dist` (`package.json` says 1.4.0). No pi was run and nothing under `~/.pi` was written, so "what a wrapped pi writes" comes from source, not a trace.

| Path under the agent dir | pi writes it when | pi loads from it | Source |
| --- | --- | --- | --- |
| `auth.json` | a token refresh, a login | **credentials; a `key` starting with `!` is run as a shell command** | `core/auth-storage.js`, `core/resolve-config-value.js` |
| `auth.json.lock/` (a folder) | every read or write of `auth.json` | nothing | `core/auth-storage.js` |
| `settings.json`, `settings.json.lock/` | a setting changes | config: `packages`, `npmCommand`, extensions | `core/settings-manager.js:795`, `core/settings-schema.js:181` |
| `models-store.json` (+ lock) | a provider catalog refresh | the model catalog | `core/models-store.js`, `core/model-runtime.js` |
| `models.json` | never | custom providers; `apiKey` and header values are literals unless they start with `!` or name a `$VAR` | `core/model-runtime.js`, `core/resolve-config-value.js` |
| `bin/fd`, `bin/rg` | the `find` or `grep` tool, when the binary is in neither `bin/` nor `PATH` | **executed, ahead of `PATH`** | `utils/tools-manager.js` |
| `extensions/`, `themes/`, `prompts/`, `skills/`, `trust.json` | not in `-p` mode | code and config | `config.js`, `core/trust-manager.js` |
| `sessions/` | never with `--session-dir` | nothing | `config.js` |

- **Why narrowing fails.** `auth.json` must be writable for a token refresh, and a `!command` key written there runs in the next unwrapped pi (the engineer's, and SSSF's `pi auth print-bearer-token`). `bin/` has the same shape. The file pi must write is also one it loads commands from, so the spec moves wrapped agents off `~/.pi` altogether.
- **pi's `models.json` path is fixed to the agent dir** (`config.js:504`), so a wrapped pi with its own agent dir needs a copy to keep custom providers.
- **`pi auth` loads no extensions.** `main.js:460` runs the auth command and returns before resources load.
- **What `--no-extensions` keeps.** `core/resource-loader.js:409`: with `noExtensions` the extension list is the `-e` paths only, so every `-e` path SSSF passes still loads, a roster's `extensions` included. Built-in tools are not extensions and `--tools` is unaffected.
- **What `--no-extensions` does not stop.** `core/resource-loader.js:369` and `:497` call `packageManager.resolve()` whatever the flag says. For a package in `settings.json` that isn't installed, `core/package-manager.js:1014-1019` installs it through `getNpmCommand()` (`:1448`), which reads `settings.json`'s `npmCommand`. So a `settings.json` in the agent dir with `packages` and `npmCommand` runs a command at every pi start. The package's own extensions are then not loaded (line 409), but the command has run. Only `PI_OFFLINE`/`--offline` stops the install (`package-manager.js:1015`); see For the engineer.
- **Where a token goes.** `pi-ai`'s `env-api-keys.js`: `openai` reads `OPENAI_API_KEY`; `anthropic` reads `ANTHROPIC_OAUTH_TOKEN` ahead of `ANTHROPIC_API_KEY`.

## Threat model

- A pi agent under `local_guard: agentsh` runs as the engineer's user and writes wherever the pi policy allows: on the baseline, all of `~/.pi`.
- pi starts outside the wrap: the engineer's own pi, `agent_pi._pi_catalog` (`pi --list-models`), `agent_pi.vm_token`, `trial.py` (`pi auth check`) and `container/pi_token_broker.py`. The first three get `.env`'s keys.
- Every agent of every run, on either coding agent, may read, write **and rename** the whole sessions tree (`sssf-sessions`), and in a plain run everything in `${PROJECT_ROOT}`, the folder itself included (`sssf-workspace`). So a deny on one path inside those trees doesn't hold: an agent renames a folder above it and reads the file at its new path. That is the route spec review 1 found in v1. A file that must stay from Claude Code agents can't live anywhere a Claude agent may rename one of its parent folders.
- The Claude policy allows nothing at `~/.local/state/sssf-pi`, nor a write, rename, link or delete of any folder above it (`~/.local/state`, `~/.local`, `~`, `/home`, `/`): `sssf-mkdir-parents` allows `mkdir` on some of them, the read rules allow looking at some, and everything else falls to `sssf-default-deny`. Runs can overlap (a worktree run beside a plain one), so this holds for an agent of any run, during another run's call.
- After this change a wrapped pi agent can still write its run's pi agent dir and any other run's, in any repo on the machine. What it leaves there (`settings.json` with `packages` and `npmCommand`, `bin/fd`, `models-store.json`) is read and run only by wrapped pi agents, inside the wrap, with an agent's environment.
- The guard can't stop what agentsh doesn't trace (xattr), does nothing under `local_guard: none`, and can't keep an agent that may read a file (a pi agent, the copy) from copying its content to a place another agent reads.
- The rules are judged on a path after agentsh resolves links, as every existing deny assumes (`sssf-other-sign-in`, `sssf-credentials`). This plan leans on nothing more than that and default-deny.

## Baseline

`main` at `2e91738`. Every finding is judged against it. There: `PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]` with every operation in the pi policy's `sssf-agent-write`; `_pi_catalog` runs `[PI_PATH, "--list-models"]`; an agent's pi command has no discovery flag; SSSF writes nothing for a pi agent but its session files and raw output; a Claude Code agent can't read `~/.pi/agent/models.json` (`sssf-default-deny`) or `auth.json` (`sssf-other-sign-in`); a pi agent can read and write both.

## Bar

Against the baseline, under `local_guard: agentsh`:

- **No agent file kept under `~/.pi`.** No rule in the pi policy allows a write, create, rename, link or mkdir there.
- **No agent code run on the host through pi.** `_pi_catalog` and every agent's pi carry `--no-extensions`.
- **No engineer file deleted or overwritten.** SSSF writes only under `~/.local/state/sssf-pi`, a folder it makes itself and no agent can replace, through `session_files.write`, which never follows a link below that folder.
- **No engineer file reaches an agent that couldn't read it on the baseline.** Under the Claude policy, in a plain run and a worktree run, every read of the `models.json` copy, of `auth.json` and of the agent dir decides `deny`, and so does a rename, link, symlink, delete or write of the agent dir and of every folder above it. Nothing SSSF writes for a pi agent under the data root holds the copy. The copy holds `{}` once the call has ended.
- **A broken agent dir stops cleanly.** A folder, or anything else SSSF's write can't replace, at the agent dir's `auth.json` or `models.json` raises `SandboxFailed` naming the path, and the wrap is never started.
- **The sign-in**, by the call's provider, as 2738b794: `openai` or `anthropic` get a token from the host's `pi auth print-bearer-token`; with no host credential and no key in the environment, `SandboxFailed` names the provider. Another provider whose host credential is a stored sign-in (`authType` `oauth`) stops with `SandboxFailed`. Another provider with a key uses the environment's. `SSSF_PI_AUTH=subscription` still keeps API keys from the agent. The token is never printed and never written to a file.
- **The Claude policy is unchanged.** For each shipped roster it equals the baseline's, rule for rule. In the pi policy only `sssf-agent-write`'s paths change and `sssf-agent-read` is added.
- Every existing test passes on the host, apart from the ones this plan rewrites by name.
- Never: a `config.md` sentence that says `--no-extensions` stops a `settings.json` package.

## Who can read each file SSSF writes for a pi agent

`<dir>` is `~/.local/state/sssf-pi/<adw_id>`.

| File | What's in it | Claude Code agent (any run, plain or worktree) | pi agent (any run, any repo) | Against the baseline |
| --- | --- | --- | --- | --- |
| `<dir>/models.json` | a copy of the engineer's `models.json`, which may hold literal keys; `{}` after the call | **denied**, `sssf-default-deny`; no rename of `<dir>` or a folder above it is allowed either | allowed (`sssf-agent-write`) | The same: a pi agent reads the original through `PI_WRITE`; a Claude agent can't |
| `<dir>/auth.json` | `{}` | denied, the same way | allowed | Tighter: on the baseline a pi agent reads the real `auth.json` |
| pi's session files (`--session-dir`) and `raw_output.jsonl`, in the sessions tree | the agent's own conversation | allowed | allowed | Unchanged; not touched by this plan |
| the sign-in token | environment only | no (`sssf-proc-secrets` denies `/proc/*/environ`) | the agent's own | New; never in a file |

The spec does **not** ask for a deny against another run's pi agent: on the baseline every pi agent reads `~/.pi/agent/models.json`, so the copy tells it nothing new. A test pins that this read is allowed, so the choice is on record.

**Why this folder, and not review 1's option (a).** Option (a) keeps the copy in the sessions tree and denies `rename`, `link` and `delete` on each folder above it. That list has to be complete for every `data_dir` a repo may set, and in a plain run it has to include `${PROJECT_ROOT}` itself, which `sssf-workspace` lets an agent rename, for instance into `/tmp`, where `sssf-temp` then allows the read. It also depends on how agentsh judges the two paths of a rename and of an exchange, which this run can't test. Option (b) depends only on default-deny: the Claude policy has no allow that could match the folder or a parent, so there is nothing to enumerate, and the Claude policy doesn't change at all. The cost is one changed allow in the pi policy and a folder in the engineer's home that SSSF makes.

**Worktree runs.** The folder's rules use `${HOME}` only, never `${PROJECT_ROOT}` or the sessions path, so the same rules decide for a plain run and a worktree run, in every repo.

**Why blank the copy as well.** No rule needs it now. It is there so literal keys don't stay in a second file on disk once no pi is reading them.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

- Add `PI_HOMES = ".local/state/sssf-pi"`, with a comment: the pi agent dirs of wrapped agents, one per run, under the home folder because no Claude Code agent may read it or write, rename or link any folder above it; SSSF makes it, and no policy lets an agent replace the folder itself.
- `PI_WRITE` becomes `[f"${{HOME}}/{PI_HOMES}/**"]`: what is inside the folder, not the folder. Nothing in `~/.pi`.
- Add `PI_READ = ["${HOME}/.pi/agent/bin", "${HOME}/.pi/agent/bin/**", f"${{HOME}}/{PI_HOMES}"]`, with a comment: pi's managed `fd` and `rg`, read and run by agents, never written, and the homes folder itself, which pi may stat on its way in. Everything else in `~/.pi` falls to `sssf-default-deny`, the sign-in file included.
- Add `def pi_home(adw_id: str, home: Optional[str] = None) -> str`: `os.path.join(home or str(Path.home()), PI_HOMES, adw_id)`. `agent_pi` and the tests both call it, so the path a test judges is the path SSSF writes.
- In `file_rules`, the `"pi"` entry becomes `(CLAUDE_SIGN_IN, [], PI_READ, PI_WRITE)`. Nothing else in the function changes: `sssf-agent-write` is still always emitted, and `sssf-agent-read` now appears for pi because `reads` is no longer empty.
- Update the comment block above `CLAUDE_WRITE` and the module docstring where they describe pi's writes.

### 2. `docs/designs/agentsh/deny-policy.py`

`test_file_rules_equal_the_designs_deny_policy_output` compares this script's output with `file_rules`. Mirror the new `PI_WRITE`, `PI_READ` and the `"pi"` entry there.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

As 2738b794 unless marked **new**.

- **Host call.** `HOST_FLAGS = ("--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files")`. `_pi_catalog` runs `[PI_PATH, "--list-models", *HOST_FLAGS]`. `env` stays `_in_container(operator_env(), sandbox.LOCAL)`. A model list needs none of the five.
- **Other host starts.** `vm_token`, `trial.py`'s `pi auth check` and `container/pi_token_broker.py` run `pi auth`, which returns before resources load (`main.js:460`); their argv is unchanged, with a one-line comment at `vm_token`. `kill.py` imports `PI_PATH` to match processes and starts nothing. `templates/harness_engineering/subagents.ts` starts pi inside an agent, already with `--no-extensions`. `scripts/install.py` only prints a `pi auth check` line. No other place starts pi.
- **Agent call.** `AGENT_FLAGS = ("--no-extensions", "--no-prompt-templates", "--no-themes")`, added to `cmd` after `--system-prompt` and before `--tools`, on every tree and under every `local_guard`. The `-e` loop and the refuse extension last are untouched. In a VM the refuse extension is copied to the same path and passed with `-e` (`sandbox_remote.install`), so it still loads. Skills and context files stay on: they are the text a repo gives its agents (`AGENTS.md`, `CLAUDE.md`, project skills), the engineer's global copies can no longer be written, and the project's are in `protected_files`. Prompt templates and themes are off because a `-p --mode json` call uses neither.
- **The host token.** `_host_token(provider, check=False)`, `SIGN_IN`, `API_KEY_VAR` and `_sign_in(env, provider)` as 2738b794. `vm_token` calls `_host_token("openai")` and keeps its messages.
- **The agent's own dir.** `_own_agent_dir(env, tree, request, provider)`, called in `run` after `_in_vm` and before `_in_container`. It returns `env` unchanged unless `tree is sandbox.LOCAL` and `request.local_guard` is an agentsh guard. Otherwise, in this order (**new**: the sign-in comes first, so nothing is on disk if it stops; the folder is not in the sessions tree):
  1. Unless `SSSF_PI_TOKEN_SOCKET` is set, `_sign_in(env, provider)`.
  2. **new:** `homes = _pi_homes()` and `agent_dir = agentsh_guard.pi_home(request.adw_id)`. If `request.adw_id` isn't one plain path part (`re.fullmatch(r"[A-Za-z0-9_-]+", ...)`), raise `SandboxFailed` naming it.
  3. `_write_own(Path(agent_dir) / "auth.json", "{}", homes)`.
  4. Read the host's `models.json` (`Path(MODELS_JSON)`) with `_read_regular`; a missing file gives `b"{}"`; any other `OSError` raises `SandboxFailed` naming `MODELS_JSON`. Then `_write_own(Path(agent_dir) / "models.json", <bytes>, homes)`. The bytes are not parsed.
  5. `env["PI_CODING_AGENT_DIR"] = agent_dir`, and append `str(Path(MODELS_JSON).parent / "bin")` to `env["PATH"]`, last.
- **new: `_pi_homes() -> str`.** The folder `Path.home() / agentsh_guard.PI_HOMES`, made the way `agentsh_guard.start` makes the agent cache: `os.makedirs` of its parent with `exist_ok`, `os.mkdir(homes, 0o700)` ignoring `FileExistsError`, then `os.lstat`. If it isn't a real folder, or any step raises `OSError`, raise `SandboxFailed` naming the path and saying to look at it with `ls -la`. It is the `root` every write below it is given, so `session_files` opens each folder under it without following a link, and a link an agent left at `<homes>/<adw_id>` is replaced, not followed.
- **new: `_write_own(path, data, root)`.** Calls `session_files.write` and turns any `OSError` (`IsADirectoryError`, `session_files.LinkedPath`, a non-empty folder) into `sandbox.SandboxFailed`: the path, that SSSF can't write it because something that isn't a file is there, that an agent may have put it there, and to look at it with `ls -la`, remove it and start again. `from None`, no traceback.
- **new: `_blank_copy(request, tree)`.** Under the same condition as `_own_agent_dir`, `session_files.write(<agent_dir>/models.json, "{}", <homes>)`, swallowing `OSError` and never making the homes folder. It never raises, so it can't hide the call's own outcome. Call it:
  - in `run`'s existing `finally`, right after `result.returncode = process.wait()`;
  - and if `_in_container` or `agentsh_guard.start` raises after `_own_agent_dir` returned: wrap those two lines in `try/except BaseException: _blank_copy(...); raise`.
- Docstrings: the module docstring, `_pi_catalog`, `vm_token`, `run`, and the new functions. `run`'s says that under an agentsh guard the agent has its own agent dir in `~/.local/state/sssf-pi` and a host-issued sign-in, reads nothing in `~/.pi` but `bin/`, and that the `models.json` copy is blanked when the call ends.

### 4. `.claude/skills/sssf/tests/test_agentsh_guard.py`

All with `shipped_text` and `Rules`, as the file's other policy tests.

- **The decoy.** Under the pi policy, for each op in `WRITE_OPS`: `${HOME}/.pi/agent/extensions/x.js`, `${HOME}/.pi/agent/settings.json`, `${HOME}/.pi/agent/auth.json`, `${HOME}/.pi/agent/bin/rg` and `${HOME}/.pi/sssf-decoy/agent/extensions/x.js` decide `deny`, and no rule whose decision is `allow` matches. With `d = agentsh_guard.pi_home("abc", HOME)`: `d/auth.json`, `d/auth.json.lock`, `d/models.json`, `d/models-store.json` and `d/bin/fd` match `sssf-agent-write` for `write`, `create`, `mkdir`, `rmdir` and `rename`. A read of `${HOME}/.pi/agent/bin/rg` is `sssf-agent-read`; a read of `${HOME}/.pi/agent/auth.json` and of `${HOME}/.pi/agent/models.json` is denied. Under the pi policy the homes folder itself (`${HOME}/.local/state/sssf-pi`) can be read but each op in `WRITE_OPS` on it is denied.
- **The copy stays private.** Under the Claude policy, with `Rules(..., root=main)` and with `Rules(..., root=WORKSPACE)`: for `d`, `d/models.json`, `d/auth.json` and `d/bin/rg`, each of `read`, `open`, `stat`, `list` and each op in `WRITE_OPS` decides `deny`.
- **No folder above it moves.** Under the Claude policy and both roots, for each of `d`, `${HOME}/.local/state/sssf-pi`, `${HOME}/.local/state`, `${HOME}/.local` and `${HOME}`: `rename`, `link`, `symlink`, `delete`, `rmdir`, `write`, `create`, `chmod` and `chown` decide `deny`. (`mkdir` is left out: `sssf-mkdir-parents` allows it on existing parents, and it moves nothing.)
- **Why the folder moved**, so the test shows what it guards: under the Claude policy, a rename of `<main>/adws/adw_data/sessions/abc` and of `<main>/adws/adw_data/sessions` is allowed under both roots, and a read of `<main>/adws/adw_data/sessions/abc/pi-agent/models.json` is allowed. That is 2738b794's path and review 1's route, and the comment says so.
- **Another run's pi agent.** Under the pi policy, a read of `d/models.json` is allowed, with a comment naming the who-reads table.
- **Only what the spec names.** For each shipped roster (`sssf.config.yaml`, `sssf.config.chatgpt.yaml`, `sssf.config.claude_code.yaml`) and each harness it has, render `policy_text` with fixed inputs and parse it. The expected value is the baseline's, written as literals in the test: each file rule's name, paths, operations and decision, in order, built from the module's unchanged constants plus the one literal `["${HOME}/.pi", "${HOME}/.pi/**"]` for the baseline's pi `sssf-agent-write`, with a comment naming `2e91738`. The Claude rules equal the expected list with nothing removed. The pi rules equal it after the test removes `sssf-agent-read` and sets `sssf-agent-write`'s paths back to that literal. `command_rules`, `network_rules` and `signal_rules` equal the baseline's. The builder checks the literals once against `git show 2e91738:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`; the test never calls git.
- Rewrite `test_pi_policy_keeps_its_shape` for the new `sssf-agent-write` paths and the new `sssf-agent-read`; `sssf-other-sign-in` unchanged. If `test_the_file_rule_order_matches_main_around_the_new_allows` or another test says the pi policy has no `sssf-agent-read`, change that one line.

### 5. `.claude/skills/sssf/tests/test_agent_pi.py`

Every test here sets `HOME` to a folder under `tmp_path` with `monkeypatch.setenv`, so nothing is written in the real home.

- **The host call.** A fake `pi` script records its argv and environment. `_pi_catalog()` (cache cleared) passes `--list-models` and every `HOST_FLAGS` entry, and the environment it got equals `operator_env()`.
- **The agent call.** With `_fake_pi` and an extension `a.ts`: argv holds each `AGENT_FLAGS` entry, `-e a.ts`, and the refuse extension as the last `-e`. The existing test that pins the order passes unchanged.
- **The own dir.** With an agentsh `LocalGuard` and `agentsh_guard.start` stubbed: the child's `PI_CODING_AGENT_DIR` equals `agentsh_guard.pi_home(<adw_id>)` and is not under `request.data_root`; the homes folder is mode `0700`; when the stub is called `auth.json` there is `{}`, even when one with an `openai` entry was there before, and `models.json` equals the dummy file `MODELS_JSON` names, which holds a literal `apiKey`; `PATH` ends with that file's sibling `bin`; no file under `data_root` holds the key. A link at `<homes>/<adw_id>` pointing outside is replaced, not followed, and its target is untouched. A link or a file at the homes folder itself raises `SandboxFailed` naming it, and the stub is never called. With `local_guard` `none`, there is no `PI_CODING_AGENT_DIR`, no `pi auth` call and no homes folder.
- **The copy is blanked.** After the call returns, `models.json` in the agent dir is `{}` and the literal key is in no file under `tmp_path` but the dummy source. The same when the stubbed `start` raises, and when the call ends by an exception from the stream loop.
- **A folder where a file goes.** With a folder at the agent dir's `auth.json`, and again at `models.json` (each also with a file inside it): the call raises `sandbox.SandboxFailed` whose message holds the path, the exception is not an `IsADirectoryError`, the `start` stub was never called, and the folder is still there. In the `models.json` case the key is in no file but the dummy source.
- **The sign-in**, each with a fake `pi` that answers `auth`, as 2738b794's tests: `openai` token in `OPENAI_API_KEY`; `anthropic` token in `ANTHROPIC_OAUTH_TOKEN` and an `sk-ant-api` value in `ANTHROPIC_API_KEY`; a failed `pi auth` with no key raises `SandboxFailed` naming the provider with the wrap never started and no `models.json` written, and with a key goes on; an API key under `SSSF_PI_AUTH=subscription` raises without the key in the message; `google` with `oauth` raises, with `api_key` or a failure goes on; `SSSF_PI_TOKEN_SOCKET` set makes no `pi auth` call.

### 6. Docs

- `docs/open.md`: delete the row "A pi agent under agentsh can plant code that runs unwrapped on the host the next time anything starts pi". Add one row for what's left: a wrapped pi agent can change any run's agent dir under `~/.local/state/sssf-pi` (`settings.json` with `packages` and `npmCommand`, `bin/`, `models-store.json`, and `auth.json` or `models.json` between SSSF's write and pi's read), which wrapped pi agents then read and run, inside the wrap only. Keep the rows on the starter harness's pi subagent and on a project's own `.pi/settings.json`.
- `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh": a "pi agents under agentsh" paragraph beside the Claude Code one:
  - They don't use `~/.pi/agent`. SSSF gives each run `~/.local/state/sssf-pi/<adw_id>`, with an empty `auth.json` and a copy of your `models.json`, written before every call; the copy is blanked when the call ends. Claude Code agents can't read the folder or move a folder above it. pi agents of any run can read it, as they could read your `models.json` before. You can delete the folder between runs.
  - They can write nothing in `~/.pi` and read only `~/.pi/agent/bin`. Your `settings.json` doesn't reach them.
  - The sign-in rules from the Bar, the 35-minute token, and that a key for any other provider goes in `.env`.
  - SSSF starts pi with discovery off: `--no-extensions` everywhere, the full list for `--list-models`, what stays on for agents and why.
  - **What `--no-extensions` leaves running:** pi still resolves the `packages` in `settings.json` and installs a missing one with `npmCommand`. For the host's `pi --list-models` that is your own `~/.pi/agent/settings.json`, which agents can no longer write, and a trusted project's `.pi/settings.json` (`docs/open.md`). For a wrapped agent it is the `settings.json` in its agent dir, which pi agents can write, so a command there runs in later wrapped agents, inside the wrap. The same goes for the agent dir's `bin`.
  - If something that isn't a file sits at the agent dir's `auth.json` or `models.json`, the call stops and names it.
  - A "Reinstall" line: the pi policy changed, so rerun `just agentsh-install`.
- `config.md`, the subscription sign-in bullet (near line 108): under agentsh the sign-in reaches a pi agent as a host-issued token, for `openai` and `anthropic`.
- `config.md`, the container's known limits (lines 985-1011). **Every one stays.** Edits:
  - Line 991 (code in `extensions/`, or a `settings.json` package): cut to what pi's source still allows. SSSF passes `--no-extensions`, so a file in the agent dir's `extensions/` no longer loads (`resource-loader.js:409`). A `settings.json` there with `packages` and `npmCommand` still runs a command at every pi start (`resource-loader.js:369`, `package-manager.js:1014`), after `check_stored_login`, and that command can plant an openai login. Still a known limit.
  - Line 989 (other sign-ins not checked) and line 999 (a swap between check and read): add that with `local_guard: agentsh` the files checked are the ones in the run's own agent dir, which pi agents can write.
  - Line 993: add "or SSSF's copy of it".
  - The paragraph at line 985: with `local_guard: agentsh`, "the `auth.json` pi reads" is the one in the run's own agent dir.
- For the documenter: any `app_docs/` write-up that says a pi agent may write `~/.pi` describes the old behaviour. The repo keeps no CHANGELOG.

## Verification

The planner and builder run under agentsh, so run the two files only, from the repo root, the way the repo's own test command does:

`uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py tests/test_agent_pi.py -m 'not pushes and not host_only' -q -p no:cacheprovider`

The planner ran it on `2e91738`: 445 passed, 1 skipped, in about 220 s. The whole suite is left to verify, on the host. Don't run a real pi against `~/.pi/agent`, and don't write anything under `~/.pi` or the real `~/.local/state`; the tests use a fake `pi`, `tmp_path` and a `HOME` under it.

Check by hand before handing in:

- `git diff 2e91738 -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows only `PI_HOMES`, `PI_WRITE`, `PI_READ`, `pi_home`, the `"pi"` entry and comments. No rule is added to or removed from the Claude policy.
- No sentence in `config.md` says a `settings.json` package doesn't load or run.

## Out of scope

Holes this change leaves open:

- The xattr gap, which is agentsh's.
- pi's trust model for a project's `.pi/` folder, past the flags above, and a trusted project's `.pi/settings.json` running a command in the host's `pi --list-models` (already in `docs/open.md`).
- The Claude policy's writes.
- VM runs, where `~/.pi` is the VM's.
- Runs with `local_guard: none`: pi agents use `~/.pi/agent` as before. They do get `AGENT_FLAGS`.
- One wrapped pi agent changing an agent dir for a later wrapped pi agent, of any run in any repo on the machine: `settings.json` (`packages`, `npmCommand`), `bin/`, `models-store.json`, or `auth.json` and `models.json` between SSSF's write and pi's read.
- A pi agent copying what it reads from the `models.json` copy to a place a Claude Code agent can read. On the baseline it can do the same with the original.
- If SSSF is killed mid-call, the copy keeps the keys until that run's next pi call. It is readable then by pi agents, which could read the original on the baseline, and by nothing a policy wraps as a Claude Code agent.
- Two pi agents of one run at the same moment: one's end blanks the copy the other is about to read, and that call fails to find a custom provider. SSSF runs a run's agents one at a time. Two runs never share a folder.
- The 35-minute token against a call's 30-minute limit, the same margin a VM run has.
- A key for a provider other than `openai` and `anthropic` that is stored in `auth.json` and not in `.env`: the wrapped pi finds no credential.
- Every container known limit in `config.md`, all kept.
- A stale `auth.json.lock` an agent leaves in an agent dir, and old runs' folders under `~/.local/state/sssf-pi`, which nothing removes.

## For the engineer

- `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agent_pi.py`, the hub's own install, are protected and stay as they are until you update the install after the merge.
- The pi policy changes and the Claude policy doesn't. Rerun `agentsh_install.py` for the hub and for pi-agent-observability.
- Repeat the decoy under the new pi policy, then run one pi-roster call. That call is the first real proof of what this plan takes from source only: that pi on the host accepts the ChatGPT token as `OPENAI_API_KEY` with an empty `auth.json`, that `--no-extensions` breaks no roster, that `find` reaches `fd` through `PATH`, and that pi starts in an agent dir whose parent it may read but not write. If pi tries to `mkdir` the parent and stops on the refusal, the fix is `PI_HOMES` in `MKDIR_PARENTS`, which changes the Claude policy too and so needs its own run. Try one `anthropic/*` pi call if you use that sign-in.
- Not done here, for you to weigh: `--offline` (or `PI_OFFLINE=1`) stops pi installing a missing `settings.json` package (`package-manager.js:1015`), which would close the `npmCommand` route. It also puts pi's model runtime offline (`core/model-runtime.js:97`), and whether `--list-models` and a wrapped call still work that way needs a real pi run, which this run may not make.
