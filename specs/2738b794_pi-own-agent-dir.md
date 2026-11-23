# Keep a pi agent from planting code in ~/.pi (v2)

## Summary

The request asks to narrow the pi policy's `PI_WRITE`. pi 1.4.0's source shows that can't be done safely: **`auth.json` is a file a wrapped pi must write and also one pi runs commands from.** So this plan takes the request's named fallback. Wrapped pi agents get an agent dir of their own through `PI_CODING_AGENT_DIR`, their sign-in comes from the host as a short-lived token, and the pi policy allows nothing under `~/.pi` except reading pi's managed `bin/`. pi is also started with discovery off, on the host and for agents.

Changed since v1, after spec review 1: the sign-in now covers `anthropic` as well as `openai` and stops by name for any other stored sign-in; the container's known limits in `config.md` are gone through one by one; the compare test uses literals; the host's `models.json` path is `MODELS_JSON`.

## What pi's source says (pi 1.4.0)

Read from `<npm global>/@earendil-works/pi-coding-agent/dist`. No pi was run and nothing under `~/.pi` was written, as the request requires, so "what a wrapped run writes" comes from source, not from a trace. The spec reviewer checked the three load-bearing claims against the same files.

| Path under the agent dir | pi writes it when | pi loads from it | Source |
| --- | --- | --- | --- |
| `auth.json` | a token refresh, a login | **credentials; a `key` starting with `!` is run as a shell command** | `core/auth-storage.js:71,147,223,383`; `core/resolve-config-value.js:66,134,161` |
| `auth.json.lock/` (a folder) | every read or write of `auth.json` (`proper-lockfile`: `mkdir`, `utimes`, `rmdir`) | nothing | `core/auth-storage.js:50,91` |
| `settings.json`, `settings.json.lock/` | a setting changes; the lock on every read when the file exists | config: `packages`, extensions | `core/settings-manager.js:150-176` |
| `models-store.json` (+ `.lock/`) | a provider catalog refresh | the model catalog | `core/models-store.js:30`, `core/model-runtime.js:85` |
| `models.json` | never | custom providers; `apiKey` may be `!command` | `core/model-runtime.js:81` |
| `bin/fd`, `bin/rg` | the `find` or `grep` tool, when the binary is in neither `bin/` nor `PATH` (downloaded) | **executed, and looked up before `PATH`** | `utils/tools-manager.js:65,76-93,301` |
| `extensions/`, `themes/`, `prompts/`, `skills/`, `trust.json` | not in `-p` mode | code and config | `config.js:499-528`, `core/trust-manager.js:174` |
| `crashes.json` | a crash | nothing loaded | `core/crash-log.js:7,24` |
| `sessions/` | never with `--session-dir` | nothing | `config.js:527` |

**Why narrowing fails.** A wrapped pi whose token expires refreshes it and writes `auth.json`. If that write is refused after the refresh, the rotated refresh token is lost and the engineer's login breaks. If it is allowed, an agent can write `{"openai": {"type": "api_key", "key": "!<command>"}}`, and the next unwrapped pi that resolves the key runs the command: the engineer's own pi, and SSSF's `pi auth print-bearer-token` (`vm_token`, `container/pi_token_broker.py`), which runs with `operator_env()`. `bin/` has the same shape: pi must write it to install `fd`, and runs what is there.

**The `auth` subcommand loads no extensions.** `main.js:460` runs `runAuthCommand` and returns before `createAgentSessionServices` (`main.js:602`), where extensions load. `--list-models` goes through that loader (`main.js:602`, then `720`).

**What `--no-extensions` keeps.** `core/resource-loader.js:409`: with `noExtensions`, the extension list is the `-e` paths only (`additionalExtensionPaths`, lines 370 and 414). Every `-e` path SSSF passes still loads, roster `extensions` included. Built-in tools (`core/tools/`) are not extensions, and `--tools` is unaffected. pi's help says the flag also turns off pi's built-in extensions (the MCP one, `dist/extensions/mcp`). The builder confirms that at `resource-loader.js:409-410` and writes what it finds in `references/config.md`.

**Where a token goes.** `pi-ai/dist/env-api-keys.js:68-83`: `openai` reads `OPENAI_API_KEY`; `anthropic` reads `ANTHROPIC_OAUTH_TOKEN` ahead of `ANTHROPIC_API_KEY`. `pi auth check --provider <p> --json` answers `{"status": "ready", "authType": ...}` (`cli/auth-check.js:30`), and `trial.py` already calls it on the host.

## Threat model

- A pi agent under `local_guard: agentsh` runs as the engineer's user. It can write wherever the pi policy allows. On the baseline that is all of `~/.pi`.
- pi starts outside the wrap: the engineer's own pi, `_pi_catalog` (`pi --list-models`), `vm_token`, `trial.py` (`pi auth check`) and `container/pi_token_broker.py`. The first three get `.env`'s keys.
- After this change an agent can still write its run's own pi agent dir, `adws/adw_data/sessions/<adw_id>/pi-agent`, and any other run's, since the policy allows all of `sessions/`. What it leaves there is read only by wrapped pi agents, inside the wrap, with an agent's environment. This guard can't stop one agent from changing another's pi config there, the same as the agents' tool cache.
- The guard can't stop what agentsh doesn't trace (xattr), and it does nothing when `local_guard` is `none`.

## Baseline

`main` at `af86ac7`. Every finding is judged against it. There, `PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]` with every operation, `_pi_catalog` runs `[PI_PATH, "--list-models"]`, and an agent's pi command has no discovery flag.

## Bar

Against the baseline, under `local_guard: agentsh`:

- No agent file is kept under `~/.pi`: no rule in the pi policy allows a write, create, rename, link or mkdir there.
- No agent code runs on the host through pi: `_pi_catalog` and every agent's pi carry `--no-extensions`.
- No engineer file is deleted or overwritten: SSSF writes only inside the session folder, through `session_files.write`.
- **The sign-in a wrapped pi agent gets**, decided by the call's provider:
  - `openai` or `anthropic`: a token from the host's `pi auth print-bearer-token --provider <provider>`, in the environment. If the host has no credential for it and the environment has no key for it, `SandboxFailed` names the provider before pi starts.
  - Any other provider whose host credential is a stored sign-in (`authType` `oauth`): `SandboxFailed` names the provider before pi starts. SSSF never starts a wrapped pi that would find no sign-in where the baseline had one.
  - Any other provider with a key: the key from the environment, as on the baseline.
  - `SSSF_PI_AUTH=subscription` still keeps API keys for `openai` and `anthropic` from the agent.
- Every other rule in the pi and Claude policies is as on `af86ac7`, for each shipped roster.
- Every existing test passes on the host, apart from the two this plan rewrites by name.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

- Delete `PI_WRITE`. Add `PI_READ = ["${HOME}/.pi/agent/bin", "${HOME}/.pi/agent/bin/**"]` with a comment: pi's managed `fd` and `rg`, read and run by agents, never written; everything else in `~/.pi` falls to `sssf-default-deny`, the sign-in file included.
- Add `PI_HOME = "pi-agent"` beside `CLAUDE_HOME`: the run's pi agent dir, in its session folder.
- In `file_rules`, the `"pi"` entry becomes `(CLAUDE_SIGN_IN, [], PI_READ, [])`. Emit `sssf-agent-write` only `if writes:`, as `sssf-agent-read` is already emitted only `if reads:`. The pi policy then has `sssf-agent-read` (the `READ` operations) where the Claude policy has it, and no `sssf-agent-write`. Nothing else in the function changes.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- **Host call.** Add `HOST_FLAGS = ("--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-context-files")`. `_pi_catalog` runs `[PI_PATH, "--list-models", *HOST_FLAGS]`. `env` stays `_in_container(operator_env(), sandbox.LOCAL)`. A model list needs none of the five.
- **Other host starts.** `vm_token` and `trial.py`'s `pi auth check` keep their argv; add a one-line comment at `vm_token` that `pi auth` returns before pi loads extensions (`main.js:460`). `container/pi_token_broker.py` and the scripts under `docs/designs/agentsh/` are not changed: the broker runs `pi auth`, and the design scripts are the engineer's manual probes.
- **Agent call.** In `run`, add `AGENT_FLAGS = ("--no-extensions", "--no-prompt-templates", "--no-themes")` to `cmd` after `--system-prompt` and before `--tools`, under every `local_guard`. The `-e` loop and the refuse extension last are untouched. Skills and context files stay on: they are text the repo gives its agents (`AGENTS.md`, `CLAUDE.md`, project skills), the engineer's global copies can no longer be written, and the project's are covered by `protected_files`. Prompt templates and themes are off because a `-p --mode json` call uses neither.
- **The host token.** Factor `vm_token`'s subprocess call into `_host_token(provider) -> tuple[int, str]` (`[PI_PATH, "auth", "print-bearer-token", "--provider", provider, "--min-expiry", f"{VM_TOKEN_MINUTES}m"]`, `operator_env()`), returning the exit code and the stripped output. `vm_token` calls it with `"openai"` and keeps its messages.
- **The agent's own dir.** Add `_own_agent_dir(env, tree, request, provider)`, called in `run` after `_in_vm` and before `_in_container`. It returns `env` unchanged unless `tree is sandbox.LOCAL` and `request.local_guard` is an agentsh guard. Otherwise, on every call:
  1. `agent_dir = os.path.abspath(Path(request.data_root) / "sessions" / request.adw_id / agentsh_guard.PI_HOME)`.
  2. `session_files.write(Path(agent_dir) / "auth.json", "{}", request.data_root)`, so a login an agent left there is replaced.
  3. Copy the host's `models.json`, `Path(MODELS_JSON)`, the path `context_window` reads: its bytes from `_read_regular`, written with `session_files.write` to `agent_dir/models.json`. A missing file writes `{}`. Anything else there that isn't a regular file raises `SandboxFailed`. The bytes are not parsed. Custom providers keep working, and the copy is read inside the wrap.
  4. `env["PI_CODING_AGENT_DIR"] = agent_dir`.
  5. Append `str(Path(MODELS_JSON).parent / "bin")` to `env["PATH"]`, last, so `find` and `grep` reach the engineer's managed `fd` and `rg` (`commandExists` searches `PATH`, `tools-manager.js:65`).
  6. The sign-in, unless `SSSF_PI_TOKEN_SOCKET` is set (in the container `_in_container` supplies it). `SIGN_IN = {"openai": ("OPENAI_API_KEY", "sk-", ("OPENAI_API_KEY",)), "anthropic": ("ANTHROPIC_OAUTH_TOKEN", "sk-ant-api", ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"))}`: the variable a sign-in token goes in, the prefix that marks an API key, and the variables that count as a key already in `env`.
     - **`provider` in `SIGN_IN`:** call `_host_token(provider)`. A non-zero exit or empty output: leave `env` alone if it holds one of the provider's variables, else raise `SandboxFailed` naming the provider, `pi` and `/login`. Output with the API-key prefix: under `SSSF_PI_AUTH=subscription` raise `SandboxFailed` without echoing it; otherwise set the provider's API key variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Any other output is a sign-in token: set the token variable. Never print the output.
     - **Any other provider:** run `[PI_PATH, "auth", "check", "--provider", provider, "--json"]` with `operator_env()`, as `trial.py` does. If it answers `"status": "ready"` with `"authType": "oauth"`, raise `SandboxFailed`: the provider's sign-in is stored in pi and SSSF hands a wrapped agent one for `openai` and `anthropic` only; use a key in `.env`. Any other answer, or none, leaves `env` alone.
- A key for another provider that the engineer stored in `auth.json`, not in `.env`, doesn't reach the wrapped pi, which ends with pi's own no-credentials error. `config.md` says to put it in `.env`. See Out of scope.

### 3. `.claude/skills/sssf/tests/test_agentsh_guard.py`

- **The decoy.** With `shipped_text(main, "pi")` and `Rules`, for each operation in `WRITE_OPS`: `${HOME}/.pi/agent/extensions/x.js`, `${HOME}/.pi/agent/settings.json`, `${HOME}/.pi/agent/auth.json`, `${HOME}/.pi/agent/bin/rg` and `${HOME}/.pi/sssf-decoy/agent/extensions/x.js` decide `deny`, and no rule with `decision: allow` matches. `<sessions>/abc/pi-agent/auth.json`, `auth.json.lock`, `models.json` and `models-store.json` match `sssf-sessions` for `write`, `create`, `mkdir` and `rmdir`. A read of `${HOME}/.pi/agent/bin/rg` is `sssf-agent-read`; a read of `${HOME}/.pi/agent/auth.json` is denied.
- **Only what the spec names.** For each shipped roster (`sssf.config.yaml`, `sssf.config.chatgpt.yaml`, `sssf.config.claude_code.yaml`) and each harness it has, render `policy_text` with fixed inputs and parse it. The expected value is the baseline's, written in the test as literals: each rule's name, paths, operations and decision, in order, built from the module's unchanged constants (`SECRETS`, `CREDENTIALS`, `CLAUDE_WRITE`, ...) plus the one literal `["${HOME}/.pi", "${HOME}/.pi/**"]` for the baseline's pi `sssf-agent-write`, with a comment naming `af86ac7`. The Claude rules equal the expected list. The pi rules equal it after the test removes `sssf-agent-read` and puts that `sssf-agent-write` rule back after `sssf-mkdir-parents`. `command_rules`, `network_rules` and `signal_rules` are compared the same way. The builder checks the literals once against `git show af86ac7:.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`; the test never calls git.
- Rewrite `test_pi_policy_keeps_its_shape`: no `sssf-agent-write`, `sssf-agent-read` paths equal `PI_READ` with `READ`, `sssf-other-sign-in` unchanged. In `test_the_file_rule_order_matches_main_around_the_new_allows`, `sssf-agent-write` is wanted for Claude only and `sssf-agent-read` for both.

### 4. `.claude/skills/sssf/tests/test_agent_pi.py`

- **The host call.** A fake `pi` script records its argv and environment. `_pi_catalog()` (cache cleared) passes `--list-models` and every `HOST_FLAGS` entry, and the environment it got equals `operator_env()`.
- **The agent call.** With `_fake_pi` and an extension `a.ts`: argv holds each `AGENT_FLAGS` entry, `-e a.ts`, and the refuse extension as the last `-e`. The existing test at line 779 and `test_rules.py`'s order test pass unchanged.
- **The own dir.** With an agentsh `LocalGuard` and `agentsh_guard.start` stubbed: the child's `PI_CODING_AGENT_DIR` is `<data_root>/sessions/<adw_id>/pi-agent`; `auth.json` there is `{}` even when one with an `openai` entry was there before; `models.json` is a copy of the file `MODELS_JSON` names, made again on a second call after the source changed; `PATH` ends with that file's sibling `bin`. A link at `pi-agent` pointing outside is replaced, not followed. With `local_guard` `none`, the environment has no `PI_CODING_AGENT_DIR` and no `pi auth` call is made.
- **The sign-in**, each with a fake `pi` that answers `auth`:
  - `openai`: the token lands in `OPENAI_API_KEY`.
  - `anthropic`: a token lands in `ANTHROPIC_OAUTH_TOKEN`; an `sk-ant-api` value lands in `ANTHROPIC_API_KEY`.
  - Either, with a failed `pi auth` and no key in the environment: `SandboxFailed` names the provider, and the wrap is never started. With a key in the environment: no stop, the key is kept.
  - An API key under `SSSF_PI_AUTH=subscription`: `SandboxFailed`, and the message doesn't hold the key.
  - `google` with `pi auth check` answering `oauth`: `SandboxFailed` names `google`, and the wrap is never started. Answering `api_key`, or failing: the call goes on.
  - `SSSF_PI_TOKEN_SOCKET` set: no `pi auth` call is made.

### 5. Docs

- `docs/open.md`: delete the row "A pi agent under agentsh can plant code that runs unwrapped on the host the next time anything starts pi". Add a short row for what's left: a wrapped pi agent can change a run's `pi-agent` folder, which wrapped pi agents read.
- `.claude/skills/sssf/references/config.md`, in "Running local agents under agentsh": a "pi agents under agentsh" paragraph beside the Claude Code one. It says:
  - They don't use `~/.pi/agent`. SSSF gives them `sessions/<adw_id>/pi-agent`, with an empty `auth.json` and a copy of your `models.json`, both written again before every call.
  - They can write nothing in `~/.pi` and read only `~/.pi/agent/bin`. Your `settings.json` doesn't reach them.
  - The sign-in rules from the Bar, with the 35-minute token, and that a key for any other provider goes in `.env`.
  - pi looks in `pi-agent/bin` ahead of `PATH`, and the run's agents can write it, so a `fd` or `rg` there runs in later wrapped agents, inside the wrap.
  - SSSF starts pi with discovery off: `--no-extensions` everywhere, the full list for `--list-models`, what stays on for agents and why, and what the builder found about the built-in MCP extension.
  - A "Reinstall" line: the pi policy changed, so rerun `just agentsh-install`.
- `.claude/skills/sssf/references/config.md`, the subscription sign-in bullet near line 108: add that under agentsh the sign-in reaches a pi agent as a host-issued token, for `openai` and `anthropic`.
- `.claude/skills/sssf/references/config.md`, the container's known limits (near lines 985-1001), one by one:

  | Known limit | After this change | Edit |
  | --- | --- | --- |
  | Any process in the container can get a token from the socket | Open, unchanged | none |
  | Stored sign-ins other than `openai` aren't checked | Open. With `local_guard: agentsh` the check reads `pi-agent/auth.json`, which SSSF has just written as `{}` | add that sentence |
  | Code in the agent dir's `extensions/`, or a `settings.json` package, can register an openai key | **Closed.** SSSF starts every pi with `--no-extensions`, so neither loads | delete the limit; say so in the paragraph above |
  | An openai `baseUrl`, `api` or `oauth` in `models.json` passes | Open. With agentsh it applies to the `pi-agent` copy | add "or its `pi-agent` copy" |
  | Key names only, so `"apiKey": "$OPENAI_API_KEY"` stops the run | Open, unchanged | none |
  | A deeply nested `models.json` stops with a traceback | Open, unchanged. The new copy step doesn't parse the file | none |
  | A file swapped between the check and pi's read | Open, and wider with agentsh: the files are in `pi-agent`, which agents can write, where before they were the container's own | add that sentence |

  Also in the paragraph above them: with `local_guard: agentsh`, "the `auth.json` pi reads" is the `pi-agent` one.
- Docstrings: `_pi_catalog`, `run`, `vm_token` and the module docstring of `agent_pi.py`; the comment block above `CLAUDE_WRITE` in `agentsh_guard.py`.
- For the documenter: `app_docs/` write-ups that say a pi agent may write `~/.pi` describe the old behaviour. No CHANGELOG is kept.

## Verification

From `.claude/skills/sssf`, the two files only (the planner and builder run under agentsh):

`uv run pytest tests/test_agentsh_guard.py tests/test_agent_pi.py -m 'not pushes and not host_only'`

Use the runner the repo's own test recipe uses if it differs; the planner did not run this command. The whole suite is left to verify, on the host. Don't run a real pi against `~/.pi/agent` and don't write under `~/.pi`.

## Out of scope

Holes this change leaves open:

- The xattr gap, which is agentsh's.
- pi's trust model for a project's `.pi/` folder, past the flags above.
- The Claude policy's writes.
- VM runs, where `~/.pi` is the VM's.
- Runs with `local_guard: none`, where pi agents use `~/.pi/agent` as before. They do get `AGENT_FLAGS`.
- One agent changing a run's `pi-agent` folder for a later wrapped agent: its `settings.json`, `bin/`, `models-store.json`, or `auth.json` and `models.json` between SSSF's write and pi's read.
- The 35-minute token against a call's 30-minute limit, the same margin a VM run has.
- A key for a provider other than `openai` and `anthropic` that is stored in `auth.json` and not in `.env`: the wrapped pi finds no credential. SSSF can't tell that case from a key in the environment with `pi auth check`, so it doesn't stop it by name.
- The container's known limits that the table above marks open: the socket token, unchecked other sign-ins, `baseUrl`/`api`/`oauth` in `models.json`, key names only, the deep-nesting traceback, and the swap between check and read.
- A stale lock folder (`auth.json.lock`) an agent leaves in `pi-agent`.

## For the engineer

- `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agent_pi.py`, the hub's own install, are protected and stay as they are until you update the install after the merge.
- Rerun `agentsh_install.py` for the hub and for pi-agent-observability, so the installed pi policies match.
- Repeat the decoy under the new pi policy, then run one pi-roster call. That call is the first real proof of what this plan takes from source: that pi on the host accepts the ChatGPT token as `OPENAI_API_KEY` with an empty `auth.json` (the container route does this today), that `--no-extensions` breaks no roster, and that `find` reaches `fd` through `PATH`. A Claude sign-in through `ANTHROPIC_OAUTH_TOKEN` is read from `pi-ai`'s source only; try one `anthropic/*` pi call if you use that sign-in.
