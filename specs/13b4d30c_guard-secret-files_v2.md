# Keep a repo's secret files out of agents' reach

A guard change. All code paths below are under the skill, `.claude/skills/sssf/`. The hub's own installed copy under `adws/` is protected and stays untouched (see `## For the engineer`).

Revision 2. It answers spec review 1. Built-in refusals no longer count toward `MAX_REFUSALS`. Claude Code gets one `Read(...)` spelling per file, the one checked by hand.

## Baseline

`main` at `e3bcd88`. Every finding is judged against it. At that commit:

- `templates/adws/adw_modules/utils.py` runs `load_dotenv()` at import, and `operator_env()` copies all of `os.environ`, so every `.env` key reaches every agent. `agent_cc.child_env` drops only `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`, and only on subscription runs. `agent_pi.child_env` drops `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, and only when `SSSF_PI_AUTH=subscription`.
- Nothing stops an agent's Read/Grep tool (Claude Code) or its `read`/`grep`/`find`/`ls` (pi) from reading `.env`.
- Shell commands are checked only against the repo's own `agent_rules` `forbid` patterns. `sssf_rules.ts` is loaded into pi agents only when the repo has refuse rules. Claude Code's Bash calls go through `sssf_refuse.py` only when the repo has refuse rules.
- `agents._event_forwarder` counts every refusal, whatever the rule. At `rules.MAX_REFUSALS` (3) it raises `RuleBreach`, which stops the run.

## Threat model

Agents run as the engineer's own user on the host, with the engineer's filesystem permissions. An agent can run any shell command its rules don't refuse. It can read any file its harness's tools reach, and print its own environment.

The guard is aimed at **accidental exposure**: an agent doing ordinary work runs `env`, `cat .env` or `source .env.local`, or opens `.env` with its Read tool. The secret then lands in a commit, a log, the trace or a handoff file.

This guard can't stop an agent set on getting a secret. Such an agent can build the path at runtime (`python -c`, `cat .e''nv`, `find -name '.e*' -exec cat`). It can read `/proc/<ppid>/environ` of the ADW process, which holds every `.env` key, or any other file the user can read. No in-process check bounds that. The container and remote VMs are the boundary for it, since `.env` never enters them.

## Bar

Against the baseline, the change must:

1. Keep every `.env`-sourced key out of each agent's environment. The exceptions: `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, a pi provider key (pi agents only), or a key listed under `agent_env:` in `repo.yaml`. Keys that did not come from `.env` (shell exports) pass as before.
2. Keep quality checks, `worktree_setup`, plan checks and git on the full environment (`operator_env()` unchanged).
3. Make Claude Code's Read and Grep refuse each protected file found at agent start. Pass exactly one `--disallowedTools "Read(//<absolute path>)"` per file: a concrete path, never a glob, and no second spelling. The planner checked this spelling by hand on claude 2.1.319 on 2026-11-06, with `--permission-mode bypassPermissions --safe-mode`. A `Read(//<abs>)` deny made Read refuse that file, and a file with no deny read as normal. It also covers a protected file outside the agent's cwd, such as the main checkout's `.env` when the agent works in a worktree.
4. Make pi's `read`/`grep`/`find`/`ls` refuse a protected file's path, with SSSF's refusal text, on every pi agent.
5. Refuse a shell command that names a protected file by basename or path, through the existing refuse paths, **and let the run go on**. Refusals by the built-in secret-file rule do **not** count toward `MAX_REFUSALS`, so they never raise `RuleBreach`. The rule refuses any command that names the file, a mention such as `grep -n "\.env" docs/x.md` or `git log -S .env` included, so three ordinary commands could otherwise end a run. Refusals by the repo's own refuse rules count exactly as today. Each built-in refusal is still logged as `rule_refused` and advised on the console. An agent that keeps retrying is bounded by the run's cost caps (`max_call_cost`, `max_run_cost`, `max_live_cost`, `max_daily_cost`), not by the refusal count. The stream-check backstop stays for the built-in rule: a matching call whose result isn't SSSF's refusal ran, and is a breach.
6. Leave `.env.sample` and `.env.example` readable by every route.

Against the baseline, it must never keep an agent's file, run agent code, or delete or overwrite an engineer's file. Finding protected files is read-only (a directory walk). No new process runs agent-supplied input.

## Out of scope

These are old holes the change leaves open, and a reviewer files them as old holes:

- Obfuscated access: `cat .e''nv`, `cat .en?`, variables, `python -c` building the path, `find ... -exec cat`, `xargs`.
- `/proc/<pid>/environ` of the ADW or any ancestor process.
- `.env` keys exported before the ADW starts, by direnv or `set -a; . .env` in the engineer's shell. They are shell keys to SSSF and pass as today.
- A secret file created during the run, after an agent started.
- A grep, find or ls over a whole folder that holds a protected file (ripgrep skips hidden and gitignored files by default), and `grep -r KEY .` in the shell.
- Edit and Write reading a file in order to edit it, and Claude Code tools other than Read and Grep.
- Secrets in the engineer's own shell environment, not from `.env`.
- VM runs: `.env` never enters a VM, and `sandbox_remote` already chooses what crosses.
- Protected files under the pruned folders: the data dir (`adws/adw_data/`, which holds other runs' worktrees), `.git`, `node_modules`, `.venv`, `venv` and `__pycache__`.
- False positives: an unrelated file that shares a protected file's basename is refused too, and so is a command that only mentions the name. Accepted. The agent is told to do the task another way, and the run goes on (Bar 5).
- A loop of built-in refusals is not stopped by the refusal count, only by the cost caps (Bar 5).

## Design

### Which keys came from `.env`

In `utils.py`, snapshot the keys before `load_dotenv()`:

```python
_SHELL_KEYS = frozenset(os.environ)
load_dotenv()
# Keys .env added. load_dotenv never overrides, so a key the shell exported is the shell's.
DOTENV_KEYS = frozenset(os.environ) - _SHELL_KEYS
AGENT_PREFIXES = ("SSSF_", "OBS_")
AGENT_KEYS = frozenset({"CLAUDE_CODE_OAUTH_TOKEN"})

def agent_env(env: dict[str, str], allowed: Iterable[str] = ()) -> dict[str, str]:
    """env without the keys .env set, except SSSF's own (SSSF_*, OBS_*),
    CLAUDE_CODE_OAUTH_TOKEN and `allowed`. For agents only: quality checks,
    worktree_setup and git keep operator_env() whole."""
```

`agent_env` reads the module global `DOTENV_KEYS` at call time, so tests can monkeypatch `utils.DOTENV_KEYS`.

### Which files are protected

New module `templates/adws/adw_modules/secret_files.py`. Don't name it `secrets.py`: that would shadow the stdlib module that `utils.py` imports.

- `DEFAULT_ALLOWED = {".env.sample", ".env.example"}`.
- `is_default(name)`: `name == ".env" or (name.startswith(".env.") and name not in DEFAULT_ALLOWED)`.
- `PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__"}`.
- `find(roots: Iterable[Path], patterns: list[str], skip: Iterable[Path] = ()) -> list[str]`:
  - `os.walk` each root, with followlinks False and errors ignored. Prune the `PRUNE` names and any dir in `skip`, comparing resolved paths. Never prune a root itself.
  - A file matches when `is_default(name)` is true, or when it matches any entry in `patterns`. A pattern without `/` is matched with `fnmatch.fnmatchcase` against the basename. A pattern with `/` is matched against the path from the root, in posix form, with a leading `./` stripped.
  - Return sorted, de-duplicated absolute paths (`os.path.abspath`, no symlink resolution).
- `shell_rule(files: list[str]) -> AgentRule | None`: `None` when `files` is empty. Otherwise an `AgentRule` with `on_match="refuse"`, this rule text: `"Don't read, print or source this repo's secret files (.env and the like). SSSF keeps them from agents."`, and one forbid pattern:
  `(?<![\w.-])(?:<alt>)(?![\w.-])`, where `<alt>` joins `re.escape(basename)` with `|` over the sorted unique basenames.
  - A basename covers the file's paths too (`sub/.env`, `./sub/.env.local`, absolute paths), because `/` is not in the lookbehind class.
  - `.env.sample` doesn't match `.env`, because `.` follows.
  - The pattern must compile in both Python `re` and JavaScript (`m` flag, no `u`). It does: the lookbehind is one character wide.

### Where they are found, and how refusals are counted

In `templates/adws/adw_modules/agents.py`, do this once per agent start, where `enforced` is computed and before `send`. Extract a small helper so it can be tested:

```python
def _secret_guard(run) -> tuple[list[str], Optional[AgentRule]]:
    settings = repo_settings.load(Path.cwd())
    data = run.cfg.defaults.data_dir
    roots = {Path.cwd().resolve(), Path(run.repo_root).resolve()}
    files = secret_files.find(roots, settings.secret_files,
                              skip=[root / data for root in roots])
    return files, secret_files.shell_rule(files)
```

In `execute`:

```python
protected, builtin = _secret_guard(run)
enforced = list(repo_settings.load(Path.cwd()).agent_rules)
if builtin:
    enforced.append(builtin)
```

The existing `with_agent_rules` system-prompt text stays as it is. The built-in rule is not added to the prompt.

`_event_forwarder` gains a keyword parameter `uncounted: tuple = ()`. These are rules whose refusals don't count toward `MAX_REFUSALS`. Change the result branch:

```python
rule, pattern, command = pending.pop(call_id)
if not rules.refused(text):
    raise rules.RuleBreach(agent_name, rule, pattern, command)
if not any(rule is u for u in uncounted):
    refusals += 1
# log rule_refused (payload "refusals": the current count) and advise, as now
if refusals >= rules.MAX_REFUSALS:
    raise ...
```

Compare by identity (`is`), not equality: a repo rule that happens to be equal to the built-in one must still count. `execute` passes `uncounted=(builtin,) if builtin else ()` when it builds the forwarder. Update the forwarder's docstring: built-in secret-file refusals are not counted, so a mention of `.env` never ends a run, and the cost caps bound a loop. The non-refused branch (a breach) is unchanged for the built-in rule.

`AgentRequest` (`templates/adws/adw_modules/data_types.py`) gains:

```python
secret_files: list[str] = Field(default_factory=list)  # absolute paths, found at agent start
agent_env: list[str] = Field(default_factory=list)     # repo.yaml agent_env: .env keys agents keep
```

`send` fills them: `secret_files=protected, agent_env=settings.agent_env`.

### repo.yaml settings

`templates/adws/adw_modules/repo_settings.py` `RepoSettings` gains:

```python
# Paths or globs, from the repo root, of files agents must not read, on top of
# .env and .env.* (but .env.sample, .env.example). A pattern without / matches a
# file name anywhere (secret_files.py).
secret_files: list[str] = Field(default_factory=list)
# Keys from .env that agents still get, besides SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN
# and, for pi, its provider keys (utils.agent_env).
agent_env: list[str] = Field(default_factory=list)
```

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/utils.py`

Add `_SHELL_KEYS`, `DOTENV_KEYS`, `AGENT_PREFIXES`, `AGENT_KEYS` and `agent_env()` as in Design. Leave `operator_env()` unchanged, but add one line to its docstring: agents get `agent_env(operator_env())`, everything else gets all of it.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/secret_files.py` (new)

As in Design. The module docstring states the bar (accidental exposure) and says this is not a boundary.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

Add `secret_files` and `agent_env` as in Design.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

Add `secret_files` and `agent_env` to `AgentRequest`.

### 5. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- Add `_secret_guard`, and append the built-in rule to `enforced`, as in Design.
- Add the `uncounted` parameter to `_event_forwarder`, and pass `(builtin,)` from `execute`.
- Fill the two new request fields.
- Import `secret_files`.

### 6. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- `child_env(agent_name="", adw_id="", allowed=())`: `env = mark_agent(agent_env(operator_env(), allowed), agent_name, adw_id)`, then the existing subscription strip. Claude Code gets no provider keys beyond `allowed`. `check_login` keeps calling `child_env()`.
- `run`: call `child_env(request.agent_name, request.adw_id, request.agent_env)`.
- `_command`: after `--tools`, add `cmd += ["--disallowedTools", f"Read(/{path})"]` for each `path` in `request.secret_files`. `path` is absolute, so this is Claude Code's `//<abs>` form. Pass one rule per file, no other spelling and no glob.
- Update the module docstring. Protected files are denied to Read and Grep with `--disallowedTools "Read(//<abs>)"`, which `--safe-mode` and `bypassPermissions` keep. Say this was checked by hand on 2.1.319.
- `_with_refusals` is unchanged. The built-in rule arrives in `request.refuse_rules`, so the shell prefix is set exactly when there is a refuse rule.

### 7. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Add `PI_PROVIDER_KEYS`, the provider key names pi reads (from pi-ai's `env-api-keys.js`): `AI_GATEWAY_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_OAUTH_TOKEN, ANT_LING_API_KEY, AZURE_OPENAI_API_KEY, BASETEN_API_KEY, CEREBRAS_API_KEY, CLOUDFLARE_API_KEY, COPILOT_GITHUB_TOKEN, DEEPSEEK_API_KEY, FIREWORKS_API_KEY, GEMINI_API_KEY, GOOGLE_CLOUD_API_KEY, GROQ_API_KEY, HF_TOKEN, KIMI_API_KEY, META_API_KEY, MINIMAX_API_KEY, MINIMAX_CN_API_KEY, MISTRAL_API_KEY, MOONSHOT_API_KEY, NVIDIA_API_KEY, OPENAI_API_KEY, OPENCODE_API_KEY, OPENROUTER_API_KEY, QWEN_TOKEN_PLAN_API_KEY, QWEN_TOKEN_PLAN_CN_API_KEY, RADIUS_API_KEY, TOGETHER_API_KEY, TYPESAFE_API_KEY, XAI_API_KEY, XIAOMI_API_KEY, XIAOMI_TOKEN_PLAN_AMS_API_KEY, XIAOMI_TOKEN_PLAN_CN_API_KEY, XIAOMI_TOKEN_PLAN_SGP_API_KEY, ZAI_API_KEY, ZAI_CODING_CN_API_KEY`.
  - Leave AWS credentials out on purpose: they are general cloud credentials. A repo that uses Bedrock lists them under `agent_env`.
  - A comment says where the list comes from, and that a key pi learns later goes in `agent_env` until this list catches up.
- `child_env(agent_name="", adw_id="", allowed=())`: start from `agent_env(operator_env(), (*PI_PROVIDER_KEYS, *allowed))`. The OBS, mark and subscription logic after that is unchanged. Update the docstring.
- `run`: pass `request.agent_env`. Always append `-e REFUSE_EXTENSION` last: drop the `if request.refuse_rules:` and keep the "last" comment.
- `_with_refusals(env, refuse_rules, secret_files=())`: set `SSSF_REFUSE_RULES` when there are rules, as now. Set `SSSF_SECRET_FILES=json.dumps(secret_files)` when there are files. Call it with `request.secret_files`.
- `_in_vm`: install `REFUSE_EXTENSION` on every remote tree, not only with refuse rules, since the command always loads it.
- `_pi_catalog` keeps `operator_env()`: it is not an agent.

### 8. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

- Update the header: the extension is loaded into every pi agent. It also blocks `read`, `grep`, `find` and `ls` on a protected file, from `SSSF_SECRET_FILES` (absolute paths found at agent start).
- Read `SSSF_SECRET_FILES` (a JSON list, default `[]`) into a `Set` of `path.resolve`d paths. Add each one's `fs.realpathSync` too when the file exists (in a try/catch).
- In the `tool_call` handler, before the bash check, handle `event.toolName` of `read`, `grep`, `find` or `ls` with a non-empty string `input.path`:
  - Normalize the path as pi does: strip one leading `@`, expand `~` or `~/` to `os.homedir()`, then `path.resolve(process.cwd(), p)`. Also take its `realpathSync` when it exists.
  - If either form is in the set, return `{ block: true, reason: \`${REFUSED}: ${input.path} is one of this repo's secret files, which SSSF keeps from agents. Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it.\` }`.
- The bash branch is unchanged. It works with an empty rule list.

### 9. `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

No code change: it already refuses any rule in `SSSF_REFUSE_RULES`, and the built-in rule arrives there. If the docstring says the prefix is used only for "a repo's refuse rules", extend it to "and SSSF's built-in secret-file rule". Keep backslashes out of the docstring, per its own note.

### 10. `.claude/skills/sssf/templates/repo.yaml`

After `agent_rules`, add commented docs and the keys:

```yaml
# Files agents must not read, on top of .env and every .env.* but .env.sample
# and .env.example, anywhere in the repo or a run's worktree. Paths from the
# repo root or globs; a pattern without / matches a file name anywhere. Found
# when each agent starts. Claude Code's Read and Grep and pi's read, grep, find
# and ls refuse them, and so does a shell command that names one. Those
# refusals never stop the run.
#   secret_files:
#     - config/credentials.json
#     - '*.pem'
secret_files: []
# Keys from .env that agents still get. By default an agent gets only SSSF_*,
# OBS_* and CLAUDE_CODE_OAUTH_TOKEN from .env, and a pi agent its provider
# keys too. Your shell's own exports and quality checks are not affected.
#   agent_env:
#     - DATABASE_URL
agent_env: []
```

### 11. `.claude/skills/sssf/templates/env.sample`

- Around line 38 (`SSSF_CLAUDE_AUTH`, where it says `api_key: pass ANTHROPIC_API_KEY through`), add: with `api_key`, export `ANTHROPIC_API_KEY` in your shell or list it under `agent_env` in `repo.yaml`, because Claude Code agents don't get it from `.env` alone.
- Near the top, add one line: only SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN and pi's provider keys reach agents from this file; see `agent_env`.

### 12. Tests (all under `.claude/skills/sssf/tests/`)

- `conftest.py`: add an autouse fixture, `monkeypatch.setattr(utils, "DOTENV_KEYS", frozenset())`, importing `adw_modules.utils` after the existing `sys.path` insert. A developer's own `.env` then never changes the suite. Tests that need `.env` keys set `utils.DOTENV_KEYS` themselves.
- New `test_secret_files.py`. Build repo settings from the shipped `templates/repo.yaml`: `yaml.safe_load` it, add the test's `secret_files`/`agent_env`, then `RepoSettings.model_validate`. Any `SSSFConfig` a test needs comes from `templates/sssf.config.yaml`, never a test-only roster.
  - **find:** in a tmp repo holding `.env`, `.env.sample`, `.env.example`, `sub/.env.local`, `node_modules/x/.env`, `adws/adw_data/worktrees/a/.env` (skipped) and `config/creds.json`, with `secret_files: ["config/*.json"]`, `find` returns exactly `.env`, `sub/.env.local` and `config/creds.json`.
  - **Worktree root:** a worktree root outside the main root is walked too.
  - **Env:** set `utils.DOTENV_KEYS` to `{"APP_SECRET", "SSSF_X", "OBS_Y", "DATABASE_URL", "FIREWORKS_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}`, and set those plus `SHELL_ONLY` with `monkeypatch.setenv`.
    - `agent_cc.child_env(allowed=["DATABASE_URL"])` lacks `APP_SECRET` and `FIREWORKS_API_KEY`, and has `SSSF_X`, `OBS_Y`, `DATABASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN` and `SHELL_ONLY`.
    - `agent_pi.child_env(allowed=["DATABASE_URL"])` has `FIREWORKS_API_KEY` and lacks `APP_SECRET`.
    - `utils.operator_env()`, which `quality.py` uses, still has `APP_SECRET`.
  - **agent_cc command:** take `agent_cc._command(request_with(secret_files=find(...)), resume=False)`. Collect every value after a `--disallowedTools` flag. They are exactly `Read(/<abs>)`, one per file, for `.env`, `sub/.env.local` and the glob's match `config/creds.json`. Each value starts with `Read(//`, and there are as many values as files. None mentions `.env.sample` or contains `*`.
  - **Refuse rule through `sssf_refuse.py`:** run the script as a subprocess, `[sys.executable, REFUSE_SHELL, line]`, with `SSSF_REFUSE_RULES=json.dumps(rules.refusing([shell_rule(files)]))` and `cwd=tmp_path`. Create the files so `cat` succeeds.
    - `cat .env` and `source ./sub/.env.local` exit 126, with stderr starting with `rules.REFUSED`.
    - `cat .env.sample` and `echo env` exit 0.
    - The Python pattern doesn't match `cat .env.example`. It does match `grep KEY sub/.env` and a line in Claude Code's shape (`... && eval 'cat .env' < /dev/null && pwd -P ...`).
  - **`_secret_guard`:** a run whose root holds `.env` gives `([<abs .env>], <AgentRule on_match=refuse>)`. A root without protected files gives `([], None)`. Use a minimal stand-in `run` with `repo_root` and `cfg` loaded from `templates/sssf.config.yaml`.
- `test_rules.py`:
  - **Built-in refusals don't stop the run (new).** With `forwarder()` built with `enforced=[repo_rule, builtin]` and `uncounted=(builtin,)`, announce and refuse `MAX_REFUSALS + 2` built-in calls such as `cat .env`. No `RuleBreach` is raised, and a `rule_refused` event is logged for each. Then refuse `MAX_REFUSALS` repo-rule calls (`git push`): the last one raises `RuleBreach` with `refusals == MAX_REFUSALS`, so the built-in refusals didn't add to the count.
  - **The backstop stays (new).** A built-in match whose result isn't the refusal still raises `RuleBreach` ("may have run").
  - **Equal is not the same (new).** A repo rule equal to the built-in one but a different object still counts.
  - Extend `forwarder()` to take `enforced` and `uncounted` if it doesn't already. The existing refusal tests stay as they are.
  - **Bun-gated, like the existing pi-extension test:**
    - Load `agent_pi.REFUSE_EXTENSION` with `SSSF_REFUSE_RULES` unset, `SSSF_SECRET_FILES=[<tmp>/.env]` and `cwd=tmp_path`. A `read` of `.env` and of `@.env` blocks, with a reason starting with `rules.REFUSED`. A `read` of `.env.sample` and a `bash` call `echo env` return undefined/null.
    - With the built-in rule's pattern in `SSSF_REFUSE_RULES`, a `bash` call `cat .env` blocks and `cat .env.sample` passes. This proves the pattern compiles in JS.
- `test_agent_pi.py`:
  - Update any test that expects no `-e <REFUSE_EXTENSION>` without refuse rules.
  - Add a test that the extension is always the last `-e`, and that `_in_vm` installs it with no refuse rules.
  - Adjust tests that call `child_env` to the new signature only if they break.
- `test_agent_cc.py`: existing tests should pass unchanged with the conftest fixture. Fix only what breaks.

### 13. Docs

- `.claude/skills/sssf/references/config.md`:
  - Add a section `## Secret files — secret_files and agent_env`, after `## Rules for every agent — agent_rules`. It covers:
    - The default protected set and its two exceptions.
    - What a `secret_files` pattern matches.
    - That files are found when each agent starts, so a file created later isn't covered.
    - The three routes:
      - Claude Code: one `--disallowedTools "Read(//<abs>)"` per file.
      - pi: `sssf_rules.ts` on `read`/`grep`/`find`/`ls`.
      - Shell: the built-in refuse rule, by basename.
    - That built-in refusals never count toward the three-refusal stop, so a command that only mentions `.env` is refused and the run goes on. The cost caps bound a loop, and the stream check still backstops the rule.
    - `agent_env` and the default key list: `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, and pi's provider keys, for pi only.
    - That quality checks, `worktree_setup` and git keep the full environment, and shell exports (direnv included) pass.
    - The bar, stated plainly. This stops accidental exposure, not an agent set on a secret, since agents run as the engineer's user. The container and VMs are the boundary, because `.env` never enters them.
    - The out-of-scope holes, briefly.
    - That an operator's `CLAUDE_CODE_SHELL_PREFIX`, or any other Claude Code setting kept in `.env`, no longer reaches the agent unless it is listed under `agent_env`.
  - `## Rules for every agent`: after the line that says a rule reads shell commands only, add one line pointing at the new section. The three-refusal stop it describes applies to the repo's own rules.
  - Line ~107 (pi subscription): add that pi agents get provider keys from `.env` only through this list.
  - Line ~491 (Claude Code Auth): `.env` keys no longer reach Claude Code agents. With `SSSF_CLAUDE_AUTH=api_key`, export `ANTHROPIC_API_KEY` in the shell or list it under `agent_env`.
- `docs/guide/customize.md` repo.yaml table (line ~88): add two rows.

  | Setting | Default | What it does |
  |---|---|---|
  | `secret_files` | `[]` | files agents must not read, on top of `.env` and `.env.*` (but `.env.sample`, `.env.example`) |
  | `agent_env` | `[]` | `.env` keys agents still get, besides `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN` and pi's provider keys |

- `docs/open.md`: delete the row "Agents can read `.env`…" (line 18), per the file's own rule for a closed item.
- This repo has no CHANGELOG. No `app_docs/` exists, so there is nothing for the documenter to flag.

## Verification

From the repo root:

1. Run the skill suite, which is the repo's quality check: `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -rs`. It must exit 0 and collect `tests/test_secret_files.py` and the new tests in `tests/test_rules.py`. `bun` is on the engineer's PATH, so the bun tests must run: `-rs` must list no new test as skipped.
2. `git diff --name-only e3bcd88` lists nothing under `adws/`, `specs/` (beyond the plan copies), `app_docs/` or any other protected path.
3. A grep shows `operator_env()` is still what `quality.py`, `worktree.py` and `plan_checks.py` pass, and `agent_env(` appears only in `agent_cc.py`, `agent_pi.py` and `utils.py`.
4. In `agent_cc.py`, `--disallowedTools` is added once per file, inside one loop, with the `Read(/{path})` form only.

## For the engineer

- The hub's own install under `adws/adw_modules/` and `adws/adw_sssf_config/` is protected. After merging, update it from the skill with `sssf-update`. Add `secret_files: []` and `agent_env: []` to `adws/adw_sssf_config/repo.yaml` if the update doesn't.
- Projects that keep `ANTHROPIC_API_KEY` in `.env` with `SSSF_CLAUDE_AUTH=api_key` must move it to the shell or `agent_env`. Say so in the update notes.
