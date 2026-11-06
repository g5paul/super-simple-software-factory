# Keep a repo's secret files out of agents' reach

A guard change. All code paths below are under the skill, `.claude/skills/sssf/`. The hub's own installed copy under `adws/` is protected and stays untouched (see `## For the engineer`).

## Baseline

`main` at `e3bcd88`. Every finding is judged against it. At that commit:

- `templates/adws/adw_modules/utils.py` runs `load_dotenv()` at import, and `operator_env()` copies all of `os.environ`, so every `.env` key reaches every agent. `agent_cc.child_env` drops only `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` on subscription runs. `agent_pi.child_env` drops `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` only when `SSSF_PI_AUTH=subscription`.
- Nothing stops an agent's Read/Grep tool (Claude Code) or `read`/`grep`/`find`/`ls` (pi) from reading `.env`.
- Shell commands are checked only against the repo's own `agent_rules` `forbid` patterns. `sssf_rules.ts` is loaded into pi agents only when the repo has refuse rules. Claude Code's Bash calls go through `sssf_refuse.py` only when the repo has refuse rules.

## Threat model

Agents run as the engineer's own user on the host, with the engineer's filesystem permissions. An agent can run any shell command its rules don't refuse, read any file its harness's tools can reach, and print its own environment.

The guard is aimed at **accidental exposure**: an agent doing ordinary work that runs `env`, `cat .env`, `source .env.local`, or opens `.env` with its Read tool, after which the secret lands in a commit, a log, the trace or a handoff file.

What this guard cannot stop at its level: an agent set on getting a secret. It can build the path at runtime (`python -c`, `cat .e''nv`, `find -name '.e*' -exec cat`), read `/proc/<ppid>/environ` of the ADW process (which holds every `.env` key), or read any file the user can. No in-process check bounds that. The container and remote VMs are the boundary for it, since `.env` never enters them.

## Bar

Against the baseline, the change must:

1. Keep every `.env`-sourced key out of each agent's environment unless it is `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, a pi provider key (pi agents only), or listed under `agent_env:` in `repo.yaml`. Keys that did not come from `.env` (shell exports) pass as before.
2. Keep quality checks, `worktree_setup`, plan checks and git on the full environment (`operator_env()` unchanged).
3. Make Claude Code's Read/Grep refuse each protected file found at agent start (one `--disallowedTools "Read(<path>)"` per file, concrete paths only, never a glob).
4. Make pi's `read`/`grep`/`find`/`ls` refuse a protected file path, with SSSF's refusal text, on every pi agent.
5. Refuse a shell command that names a protected file by basename or path, through the existing refuse paths, so the run goes on.
6. Leave `.env.sample` and `.env.example` readable by every route.

It must never, against the baseline: keep an agent's file, run agent code, or delete or overwrite an engineer's file. Finding protected files is read-only (a directory walk). No new process runs agent-supplied input.

## Out of scope

Old holes this change leaves open (a reviewer files them as old holes):

- Obfuscated access: `cat .e''nv`, `cat .en?`, variables, `python -c` building the path, `find ... -exec cat`, `xargs`.
- `/proc/<pid>/environ` of the ADW or any ancestor process.
- A secret file created during the run, after an agent started.
- A grep/find/ls over a whole folder that holds a protected file (ripgrep skips hidden and gitignored files by default); `grep -r KEY .` in the shell.
- Edit/Write tools reading a file to edit it; MultiEdit/NotebookRead and Claude Code tools other than Read/Grep.
- Secrets in the engineer's own shell environment, not from `.env`.
- VM runs: `.env` never enters a VM and `sandbox_remote` already chooses what crosses.
- Protected files under the data dir (`adws/adw_data/`, which holds other runs' worktrees), `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`: the walk prunes them.
- A protected file's basename used for an unrelated file elsewhere is refused too (false positive, accepted; the agent is told to do it another way).

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

New module `templates/adws/adw_modules/secret_files.py` (not `secrets.py`, which shadows the stdlib name `utils.py` imports):

- `DEFAULT_ALLOWED = {".env.sample", ".env.example"}`.
- `is_default(name)`: `name == ".env" or (name.startswith(".env.") and name not in DEFAULT_ALLOWED)`.
- `PRUNE = {".git", "node_modules", ".venv", "venv", "__pycache__"}`.
- `find(roots: Iterable[Path], patterns: list[str], skip: Iterable[Path] = ()) -> list[str]`: `os.walk` each root (followlinks False, errors ignored), pruning `PRUNE` names and any dir in `skip` (resolved). A file matches when `is_default(name)`, or for any `patterns` entry: a pattern without `/` is matched with `fnmatch.fnmatchcase` against the basename, and a pattern with `/` against the path relative to the root in posix form (a leading `./` stripped). Returns sorted, de-duplicated absolute paths (`os.path.abspath`, no symlink resolution).
- `shell_rule(files: list[str]) -> AgentRule | None`: `None` when `files` is empty. Otherwise an `AgentRule` with `on_match="refuse"`, rule text `"Don't read, print or source this repo's secret files (.env and the like). SSSF keeps them from agents."`, and one forbid pattern:
  `(?<![\w.-])(?:<alt>)(?![\w.-])` where `<alt>` is `|`.join of `re.escape(basename)` for the sorted unique basenames. A basename covers its paths too (`sub/.env`, `./sub/.env.local`, absolute paths): `/` is not in the lookbehind class. `.env.sample` doesn't match `.env` because `.` follows. The pattern must compile in both Python `re` and JavaScript (`m` flag, no `u`); it does with a one-character lookbehind.

### Where they are found

In `templates/adws/adw_modules/agents.py`, once per agent start (where `enforced` is computed, before `send`):

```python
settings = repo_settings.load(Path.cwd())
protected = secret_files.find(
    {Path.cwd().resolve(), Path(run.repo_root).resolve()}, settings.secret_files,
    skip=[Path.cwd() / run.cfg.defaults.data_dir, Path(run.repo_root) / run.cfg.defaults.data_dir])
enforced = list(settings.agent_rules)
builtin = secret_files.shell_rule(protected)
if builtin:
    enforced.append(builtin)
```

Keep the existing `with_agent_rules` system-prompt text unchanged (the built-in rule is not added to the prompt). Pass `enforced` to `_event_forwarder` as now, so the stream check backstops the built-in rule like any refuse rule (a match whose result isn't the refusal is a breach; `MAX_REFUSALS` applies). The `run.repo_root` worktree is not under the skip when it is the root being walked (skip only prunes the data dir *inside* each root; the worktree itself is walked as its own root). Make sure the skip test compares resolved paths and does not prune a root itself.

`AgentRequest` (`templates/adws/adw_modules/data_types.py`) gains:

```python
secret_files: list[str] = Field(default_factory=list)  # absolute paths, found at agent start
agent_env: list[str] = Field(default_factory=list)     # repo.yaml agent_env: .env keys agents keep
```

and `send` fills them: `secret_files=protected, agent_env=settings.agent_env`.

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

Add `_SHELL_KEYS`, `DOTENV_KEYS`, `AGENT_PREFIXES`, `AGENT_KEYS`, `agent_env()` as in Design. Leave `operator_env()` unchanged, but add one line to its docstring: agents get `agent_env(operator_env())`, everything else the whole of it.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/secret_files.py` (new)

As in Design. Module docstring states the bar (accidental exposure) and that this is not a boundary.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

Add `secret_files` and `agent_env` as in Design.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

Add `secret_files` and `agent_env` to `AgentRequest`.

### 5. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

Find protected files and append the built-in rule, as in Design; fill the two new request fields. Import `secret_files`.

### 6. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- `child_env(agent_name="", adw_id="", allowed=())`: `env = mark_agent(agent_env(operator_env(), allowed), agent_name, adw_id)`, then the existing subscription strip. No provider keys are allowed for Claude Code beyond `allowed`. `check_login` keeps calling `child_env()`.
- `run`: `child_env(request.agent_name, request.adw_id, request.agent_env)`.
- `_command`: after `--tools`, for each path in `request.secret_files` add the deny rules. Use two spellings per file so the rule matches however Claude Code anchors it: `--disallowedTools`, `f"Read(/{path})"` (the `//abs` form for an absolute path, since `path` already starts with `/`), and, when the path is under `request.cwd`, also `--disallowedTools`, `f"Read(./{relpath})"`. One flag-value pair per rule; never a glob. Update the module docstring: protected files are denied to Read/Grep with `--disallowedTools`, which `--safe-mode` and `bypassPermissions` keep (checked by hand on 2.1.319).
- `_with_refusals` is unchanged: the built-in rule arrives in `request.refuse_rules`, so the shell prefix is set exactly when there is a refuse rule.

### 7. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Add `PI_PROVIDER_KEYS`, the provider key names pi reads (from pi-ai's `env-api-keys.js`): `AI_GATEWAY_API_KEY, ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_OAUTH_TOKEN, ANT_LING_API_KEY, AZURE_OPENAI_API_KEY, BASETEN_API_KEY, CEREBRAS_API_KEY, CLOUDFLARE_API_KEY, COPILOT_GITHUB_TOKEN, DEEPSEEK_API_KEY, FIREWORKS_API_KEY, GEMINI_API_KEY, GOOGLE_CLOUD_API_KEY, GROQ_API_KEY, HF_TOKEN, KIMI_API_KEY, META_API_KEY, MINIMAX_API_KEY, MINIMAX_CN_API_KEY, MISTRAL_API_KEY, MOONSHOT_API_KEY, NVIDIA_API_KEY, OPENAI_API_KEY, OPENCODE_API_KEY, OPENROUTER_API_KEY, QWEN_TOKEN_PLAN_API_KEY, QWEN_TOKEN_PLAN_CN_API_KEY, RADIUS_API_KEY, TOGETHER_API_KEY, TYPESAFE_API_KEY, XAI_API_KEY, XIAOMI_API_KEY, XIAOMI_TOKEN_PLAN_AMS_API_KEY, XIAOMI_TOKEN_PLAN_CN_API_KEY, XIAOMI_TOKEN_PLAN_SGP_API_KEY, ZAI_API_KEY, ZAI_CODING_CN_API_KEY`. AWS credentials are left out on purpose (they're general cloud credentials); a repo lists them under `agent_env` for Bedrock. Comment says where the list comes from and that a key pi learns later goes in `agent_env` until this list catches up.
- `child_env(agent_name="", adw_id="", allowed=())`: start from `agent_env(operator_env(), (*PI_PROVIDER_KEYS, *allowed))`, then the existing OBS/mark/subscription logic unchanged. Update its docstring.
- `run`: pass `request.agent_env`; always append `-e REFUSE_EXTENSION` last (drop the `if request.refuse_rules:`; keep the "last" comment).
- `_with_refusals(env, refuse_rules, secret_files=())`: set `SSSF_REFUSE_RULES` when there are rules (as now) and `SSSF_SECRET_FILES=json.dumps(secret_files)` when there are files. Call it with `request.secret_files`.
- `_in_vm`: install `REFUSE_EXTENSION` on every remote tree (the command always loads it), not only with refuse rules. `_pi_catalog` keeps `operator_env()` (it is not an agent).

### 8. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

- Update the header: loaded into every pi agent; also blocks `read`, `grep`, `find`, `ls` on a protected file (`SSSF_SECRET_FILES`, absolute paths found at agent start).
- Read `SSSF_SECRET_FILES` (JSON list, default `[]`) into a `Set` of `path.resolve`d paths, plus each one's `fs.realpathSync` when it exists (try/catch).
- In the `tool_call` handler, before the bash check: when `event.toolName` is one of `read`, `grep`, `find`, `ls` and `input.path` is a non-empty string, normalize it as pi does (strip one leading `@`; `~` or `~/` expands to `os.homedir()`; `path.resolve(process.cwd(), p)`), and also its `realpathSync` when it exists. If either is in the set, return `{ block: true, reason: \`${REFUSED}: ${input.path} is one of this repo's secret files, which SSSF keeps from agents. Nothing ran. Don't try it another way: do the task without it, or say in your report that the task needs it.\` }`.
- The bash branch is unchanged; it works with an empty rule list.

### 9. `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

No code change. It already refuses any rule in `SSSF_REFUSE_RULES`; the built-in rule arrives there. If the docstring says the prefix is used only for "a repo's refuse rules", extend it to "and SSSF's built-in secret-file rule" (no backslashes in the docstring, per its own note).

### 10. `.claude/skills/sssf/templates/repo.yaml`

After `agent_rules`, add commented docs and the keys:

```yaml
# Files agents must not read, on top of .env and every .env.* but .env.sample
# and .env.example, anywhere in the repo or a run's worktree. Paths from the
# repo root or globs; a pattern without / matches a file name anywhere. Found
# when each agent starts. Claude Code's Read and Grep and pi's read, grep, find
# and ls refuse them, and so does a shell command that names one.
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

Around line 38 (`SSSF_CLAUDE_AUTH` / `api_key: pass ANTHROPIC_API_KEY through`), add: with `api_key`, export `ANTHROPIC_API_KEY` in your shell or list it under `agent_env` in `repo.yaml`; from `.env` alone Claude Code agents don't get it. Near the top, one line: only SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN and pi's provider keys reach agents; see `agent_env`.

### 12. Tests (all under `.claude/skills/sssf/tests/`)

- `conftest.py`: an autouse fixture `monkeypatch.setattr(utils, "DOTENV_KEYS", frozenset())` (import `adw_modules.utils` after the existing `sys.path` insert), so a developer's own `.env` never changes the suite. Tests that need `.env` keys set `utils.DOTENV_KEYS` themselves.
- New `test_secret_files.py`. Build repo settings from the shipped `templates/repo.yaml` (`yaml.safe_load`, then add the test's `secret_files`/`agent_env`, then `RepoSettings.model_validate`); any `SSSFConfig` a test needs comes from `templates/sssf.config.yaml`, never a test-only roster.
  - `find` in a tmp repo with `.env`, `.env.sample`, `.env.example`, `sub/.env.local`, `node_modules/x/.env`, `adws/adw_data/worktrees/a/.env` (skipped), `config/creds.json` with `secret_files: ["config/*.json"]`: returns exactly `.env`, `sub/.env.local`, `config/creds.json`.
  - A worktree root outside the main root is walked too.
  - Env, with `utils.DOTENV_KEYS = {"APP_SECRET", "SSSF_X", "OBS_Y", "DATABASE_URL", "FIREWORKS_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}` and those plus `SHELL_ONLY` set via `monkeypatch.setenv`: `agent_cc.child_env(allowed=["DATABASE_URL"])` lacks `APP_SECRET` and `FIREWORKS_API_KEY`, has `SSSF_X`, `OBS_Y`, `DATABASE_URL`, `CLAUDE_CODE_OAUTH_TOKEN`, `SHELL_ONLY`; `agent_pi.child_env(allowed=["DATABASE_URL"])` has `FIREWORKS_API_KEY` and lacks `APP_SECRET`; `utils.operator_env()` (what `quality.py` uses) still has `APP_SECRET`.
  - agent_cc command: `agent_cc._command(request_with(secret_files=find(...)), resume=False)` holds a `Read(...)` deny after a `--disallowedTools` flag for `.env`, `sub/.env.local` and the `secret_files` glob's match `config/creds.json`, and no rule mentioning `.env.sample` or containing `*`.
  - Refuse rule through `sssf_refuse.py`: run the script as a subprocess (`[sys.executable, REFUSE_SHELL, line]`) with `SSSF_REFUSE_RULES=json.dumps(rules.refusing([shell_rule(files)]))`, `cwd=tmp_path`: `cat .env` and `source ./sub/.env.local` exit 126 with stderr starting with `rules.REFUSED`; `cat .env.sample` and `echo env` exit 0 (create the files so `cat` succeeds). Also assert the Python pattern doesn't match `cat .env.example` and does match `grep KEY sub/.env` and a Claude-Code-shaped line (`... && eval 'cat .env' < /dev/null && pwd -P ...`).
  - agents.py wiring: through whatever seam the existing tests use for `agents.execute`/`send` (see `test_prompt_rules.py` / `test_rules.py`), a repo with `.env` gives a request whose `refuse_rules` holds the built-in rule and whose `secret_files` lists `.env`; a repo with none gives neither. If no cheap seam exists, test a small helper extracted in agents.py (e.g. `_secret_guard(run) -> tuple[list[str], AgentRule | None]`) instead.
- `test_rules.py` (bun-gated, like the existing pi-extension test): a new test loads `agent_pi.REFUSE_EXTENSION` with `SSSF_REFUSE_RULES` unset and `SSSF_SECRET_FILES=[<tmp>/.env]`, `cwd=tmp_path`: `read` of `.env` and of `@.env` blocks with a reason starting with `rules.REFUSED`; `read` of `.env.sample` and a `bash` call `echo env` return undefined/null. A second bun case: the built-in rule's pattern via `SSSF_REFUSE_RULES` blocks `bash` `cat .env` and spares `cat .env.sample` (proves the pattern compiles in JS).
- `test_agent_pi.py`: update any test that expects no `-e <REFUSE_EXTENSION>` without refuse rules; add one asserting the extension is always the last `-e`, and that `_in_vm` installs it with no refuse rules. Adjust tests that call `child_env` to the new signature only if they break.
- `test_agent_cc.py`: existing tests should pass unchanged with the conftest fixture; fix only what breaks.

### 13. Docs

- `.claude/skills/sssf/references/config.md`:
  - New section `## Secret files — secret_files and agent_env`, after `## Rules for every agent — agent_rules`. It covers: the default protected set and the two exceptions; `secret_files` pattern semantics; found when each agent starts (a file created later isn't covered); the three routes (Claude Code `--disallowedTools "Read(<path>)"` per file, pi's `sssf_rules.ts` on `read`/`grep`/`find`/`ls`, the built-in shell refuse rule by basename, and that the stream check backstops it); `agent_env` and the default key list (`SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, pi's provider keys for pi only); that quality checks, `worktree_setup` and git keep the full environment; that shell exports pass. State the bar plainly: this stops accidental exposure, not an agent set on a secret, since agents run as the engineer's user; the container and VMs are the boundary because `.env` never enters them. List the out-of-scope holes briefly. Note that an operator's `CLAUDE_CODE_SHELL_PREFIX` or other Claude Code setting kept in `.env` no longer reaches the agent unless listed under `agent_env`.
  - `## Rules for every agent`: where it says a rule reads shell commands only (the `.env` example), add one line pointing at the new section.
  - Line ~107 (pi subscription): add that pi agents get provider keys from `.env` only by this list.
  - Line ~491 (Claude Code Auth): `.env` keys no longer reach Claude Code agents; with `SSSF_CLAUDE_AUTH=api_key`, export `ANTHROPIC_API_KEY` in the shell or list it under `agent_env`.
- `docs/guide/customize.md` repo.yaml table (line ~88): add rows `secret_files` | `[]` | files agents must not read, on top of `.env` and `.env.*` (but `.env.sample`, `.env.example`); and `agent_env` | `[]` | `.env` keys agents still get, besides `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN` and pi's provider keys.
- `docs/open.md`: delete the row "Agents can read `.env`…" (line 18), per the file's own rule for a closed item.
- No CHANGELOG in this repo. No `app_docs/` exists; nothing for the documenter to flag.

## Verification

From the repo root:

1. The skill suite (this is the repo's quality check):
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q` exits 0, with `tests/test_secret_files.py` and the new bun-gated tests in `tests/test_rules.py` collected. With `bun` on PATH (it is on the engineer's machine), the bun tests must run, not skip: check with `-rs` that no new test is skipped.
2. `git diff --name-only e3bcd88` lists nothing under `adws/`, `specs/` (beyond this plan), `app_docs/` or any protected path.
3. A grep that `operator_env()` is still what `quality.py`, `worktree.py` and `plan_checks.py` pass, and `agent_env(` appears only in `agent_cc.py` and `agent_pi.py`.

## For the engineer

- The hub's own install under `adws/adw_modules/` and `adws/adw_sssf_config/` is protected. After merging, update it from the skill (`sssf-update`), and add `secret_files: []` / `agent_env: []` to `adws/adw_sssf_config/repo.yaml` if the update doesn't.
- Confirm once by hand on claude 2.1.319 which `Read(...)` spelling denies an absolute path: the plan passes both `Read(//abs/path)` and, for files under the agent's cwd, `Read(./rel/path)`. If one form is dead weight, drop it in a follow-up.
- Projects keeping `ANTHROPIC_API_KEY` in `.env` with `SSSF_CLAUDE_AUTH=api_key` must move it to the shell or `agent_env`. Say so in the update notes.
