# Rename `local_guard: text` to `local_guard: none`

This is the second version of the plan. It differs from `specs/7bad1e2b_local-guard-none.md` in three places, all from spec review 1: `## Out of scope` names the four `config.md` known limits, step 8 adds `config.md` line 661, and step 7 says how the new test file gets its fixtures.

## Goal

`local_guard` picks the optional layer around SSSF's base guards on the host. Its "no layer" value is called `text`, which reads as if it switched on the base guards' text checks. Rename the value to `none`, keep `text` loading as an alias, and change nothing about what `agentsh` does.

Line numbers below are the baseline's.

## Baseline

`main` at `92c7e8d`. Every finding is judged against that commit.

At the baseline:

- `RepoSettings.local_guard` is `Literal["text", "agentsh"]`, default `"text"`. A missing key gives the default. `null`, an empty value, a list, a boolean or any other string fails validation in `repo_settings.load`.
- `LocalGuard.mode` is `Literal["text", "agentsh"]`.
- `agentsh_guard.read` returns `LocalGuard(mode="text")` when the setting equals `"text"`, and takes the agentsh path for anything else. `agentsh_guard.start` starts the agent unwrapped when `guard.mode == "text"`, refuses when the guard is `None` on a local tree, and takes the agentsh path for anything else. Both fail closed: only an exact match skips agentsh.
- Every other read tests for `"agentsh"`: `agent_cc.py` 114, 164, 340; `session.py` 131, 166; `worktree.py` 337, 515; `utils.copy_installs`; `scripts/agentsh_install.py` 120.

## Threat model

The agent runs as the engineer's own user. It can write `adws/adw_sssf_config/repo.yaml` and the modules in the checkout wherever the base guards and agentsh let it, so it can set `local_guard` for the *next* run. SSSF reads the setting once, in `session.ensure`, before any agent runs, and the result travels on every `AgentRequest`. This change keeps that.

What the rename could get wrong, and must not:

- **Fail open at load.** A value that refused at the baseline (`off`, `null`, `None`, `TEXT`, a list) must not start meaning "no layer". Only the exact strings `none` and `text` mean it.
- **Fail open at launch.** A test written as "not agentsh" would run an unknown mode unwrapped. The "no layer" branch stays an exact match on `"none"`, and everything else stays on the agentsh path.
- **A missed comparison.** A leftover `== "text"` never matches after the rename. In `read` and `start` that sends a `none` repo down the agentsh path, which refuses. It fails closed, but it breaks every `none` repo, so the tests must catch it.

What this guard can't stop at its level, at the baseline and after: an agent that edits `repo.yaml` to `none` (or `text`) lowers the next run's guard. The alias gives that edit a second spelling and no new power.

## Bar

Must do:

- `local_guard: text`, `local_guard: none` and no key give the same `RepoSettings` (`local_guard == "none"`) and the same `LocalGuard(mode="none")`. Each launches a Claude Code agent and a pi agent with the same argv and environment, with no agentsh wrap.
- These refuse in `repo_settings.load`, with a message naming `none` and `agentsh`: the strings `None`, `TEXT`, `Agentsh`; `off` and `false` (YAML booleans); `null`, `~` and an empty value; a list. The match is case-sensitive.
- `LocalGuard(mode="text")` raises. No value reaches `LocalGuard` except `none` or `agentsh`.
- A request with no `LocalGuard` still refuses on a local tree. `local_guard: agentsh` with a VM sandbox still refuses.
- No `"text"` is compared against a guard mode in the Python under `.claude/skills/sssf/templates/adws/`, except the one alias test in `repo_settings.py`.

Must never do, against the baseline:

- Change what `agentsh` does. Under `local_guard: agentsh` the rendered policies, the server config, the launch argv and environment, and which checks run and refuse are byte-identical to `92c7e8d`. The one allowed difference is the wording of refusals and hints, which say `local_guard: none` where they said `local_guard: text` (the request's item 4).
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't. This change adds no file operation.
- Change an installed repo's `repo.yaml` by any path other than `install.py --update`'s usual template handling. `scripts/install.py` is not edited.

## Steps

All paths are from the repo root. The builder is `guard_builder`; its `writes` covers every path below except `GLOSSARY.md`.

### 1. Settings: `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

- Line 145-147: the field becomes `local_guard: Literal["none", "agentsh"] = "none"`. Rewrite its comment: the optional layer around the base guards for a local agent; `"none"` adds nothing; `"agentsh"` also runs it under the agentsh service; `"text"` is the old name for `"none"` and still loads; not with a VM sandbox.
- Add a `@field_validator("local_guard", mode="before")` that returns `"none"` when the value is exactly the string `"text"` (`isinstance(value, str) and value == "text"`), and returns every other value unchanged. Write it as an `==` compare, since the stale-value test allows exactly one. The `Literal` then refuses everything else, so pydantic's own message names `'none'` and `'agentsh'`. Don't lower-case, strip or coerce. Don't map `None`.
- `load` and `_guard` stay as they are.

### 2. The carried guard: `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`

- Line 514: `mode: Literal["none", "agentsh"]`.
- Line 512, the docstring: "only mode="none" means no layer".

### 3. The guard module: `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

Nine lines change, and nothing else in the file:

- 466-467: `if settings.local_guard == "none": return LocalGuard(mode="none")`.
- 1086: `if guard.mode == "none":`.
- 64 (`FIX`), 470, 593, 667, 730, 748: `local_guard: text` becomes `local_guard: none` in each message, with the surrounding backticks and words left alone.

Keep both branches as exact matches on `"none"`. Leave "the text guard" in the docstring (line 3), the comment at 354 and `rules.py` 73: there it names the text checks, not the setting's value. `rules.py`, `agentsh_sssf.yaml`, `sssf_rules.ts`, `permissions.py`, `gates.py` and `.claude/skills/sssf/scripts/agentsh_install.py` are not edited.

### 4. The harnesses

- `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` line 32, the module docstring: "Under the text guard, auth stays..." becomes "Under local_guard: none, auth stays...". No code changes.
- `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`: my search found no comment naming the old value. Search the file once more for a comment that names the `text` guard mode. If there is none, leave the file untouched and say so in your report.

Before moving on, search `.claude/skills/sssf/templates/adws/` for any place that builds a `LocalGuard` or an `AgentRequest` from saved data (`model_validate`, `model_validate_json`). I found none: `session.ensure` builds the guard afresh in every process. If one exists, stop and report it, because a saved `mode: "text"` would no longer load.

### 5. Templates

- `.claude/skills/sssf/templates/repo.yaml` line 92-95 and 105: the value becomes `local_guard: none`. The comment says: the optional layer around SSSF's base guards on this machine; `none` adds nothing, so the base guards run alone; `agentsh` also runs each agent under the agentsh service (keep the rest of that sentence). Add one line: `text` is the old name for `none` and still works.
- `.claude/skills/sssf/templates/justfile`, the `agentsh-install` recipe (comment at 280-283 and the recipe body): change any wording that names the `text` value to `none`. My search found `local_guard` only on line 280, naming `agentsh`. If nothing in the recipe names the old value, leave the file untouched and say so.

### 6. Tests that name the old mode

`LocalGuard(mode="text")` now raises, so rename the mode literal to `"none"` in:

- `.claude/skills/sssf/tests/test_agent_cc.py` line 72.
- `.claude/skills/sssf/tests/test_agent_pi.py` line 104.
- `.claude/skills/sssf/tests/test_silence.py` line 124.
- `.claude/skills/sssf/tests/test_secret_files.py` line 115.
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: 277, 313, 351, 998-1001 (the `"text"` case and the value it writes), 2071, 2417, 2442, 3020, 3178, 3236, 3419, 3429, 3523, 3683. Rename test ids and names that say `text` for the mode where it is a one-word change.

Change only lines that name the no-layer mode. No assertion about agentsh mode changes: that is the evidence that agentsh is unchanged. Leave `"text"` where it is a content part's type or a `subprocess` argument.

### 7. New test file: `.claude/skills/sssf/tests/test_local_guard_names.py`

Fixtures and helpers, so pytest registers each one in this module:

- Define the file's own `cfg` fixture, three lines, as `test_agentsh_guard.py` 61-63 does: `agents.load_config` on `.claude/skills/sssf/templates/sssf.config.yaml`, the roster SSSF ships. Never a test-only config.
- Define its own `git_repo` helper (`test_agentsh_guard.py` 55-58 is four lines).
- Put the tests folder on `sys.path` and import by name, as `test_agentsh_guard.py` 40-41 does: `from test_agent_cc import fake, request` and `from test_agent_pi import _fake_pi, _turn`. Importing the `fake` fixture by name registers it here.
- Don't import from `test_agentsh_guard.py`, and don't edit `.claude/skills/sssf/tests/conftest.py`.

Tests:

1. **The names load alike.** For `local_guard: text`, `local_guard: none` and a `repo.yaml` without the key, in a temp git repo: `repo_settings.load` gives three equal `RepoSettings` with `local_guard == "none"`, and `agentsh_guard.read(cfg)` gives three equal guards, each `LocalGuard(mode="none")`.
2. **The names launch alike.** For each of the three, put the guard `read` returned on the request and launch a Claude Code agent and a pi agent the way the `forged_run` fixture does for its unguarded cases (`test_agentsh_guard.py` 3178). Wrap `sandbox.LOCAL.start` to record `(cmd, env)` and pass the call on. Patch `agentsh_guard.check_machine` to fail the test if called. Assert the three recorded argvs are equal and the three environments are equal, per harness; that `cmd[0]` is not `agentsh_guard.BINARY`; and that the environment holds no `AGENTSH_*`, `SSSF_AGENTSH` or `CLAUDE_CONFIG_DIR` key. If an environment value differs between calls for a reason that isn't the guard, exclude that one key by name with a comment saying why.
3. **Fails closed at load.** Parametrize over `repo.yaml` bodies: `local_guard: None`, `TEXT`, `Agentsh`, `off`, `false`, `null`, `~`, `local_guard:` (empty), `local_guard: ''`, `local_guard: [none]`, `local_guard: [text]`. Each makes `repo_settings.load` raise `ValidationError` whose text holds both `none` and `agentsh`, and makes `agentsh_guard.read(cfg)` raise `SystemExit` naming `repo.yaml`.
4. **Nothing else reaches `LocalGuard`.** `LocalGuard(mode=m)` raises for `"text"`, `"None"`, `""` and `None`.
5. **agentsh still loads and still refuses a VM.** `local_guard: agentsh` loads as `"agentsh"`; with `sandbox: vercel` it refuses.
6. **No stale value.** Parse every `*.py` under `.claude/skills/sssf/templates/adws/` with `ast`. Fail on: a `Compare` with the constant `"text"` as an operand (or inside a tuple, list or set operand), unless the other side's source names `"type"` (a content part's type, as in `agent_cc.py` 194 and `agent_pi.py` 158); a call keyword `mode="text"`; a `Literal[...]` holding `"text"`. Allow exactly one such `Compare` in `repo_settings.py`, the alias. In the same test, run the finder over three sample sources (`guard.mode == "text"`, `LocalGuard(mode="text")`, `mode: Literal["text", "agentsh"]`) and assert it flags each, so the test can't pass by finding nothing.

The existing `test_no_guard_on_a_local_tree_refuses_and_a_vm_tree_is_not_wrapped` (`test_agentsh_guard.py` 287) covers the request with no `LocalGuard`. Leave it unchanged.

### 8. Docs

The value's name changes to `none` in the current-state docs:

- `docs/concepts.md`: 160 (`none`, the default, adds nothing), 197 (the table's values and default), 219, 225. Replace line 162 with one sentence: `text` is the old name for `none` and still works.
- `docs/guide/install.md`: 41 and 259.
- `docs/guide/run.md`: 323.
- `docs/guide/customize.md` 158 and `README.md` 84: "set it to `text`" becomes "set it to `none`". The request's list leaves these two out, but they tell a reader which value to write.
- `.claude/skills/sssf/references/config.md`: the settings table row at 531 (default `none`; "`none` adds nothing"), and `local_guard: text` at 571, 587, 595, 608 and 661 (661 ends "If you'd rather not, set `local_guard: text`"). Add to the 531 row that `text` is the old name and still loads. On 571, 587, 608 and 661 change the value's name only: each states a known limit, and the limit stays as written (see `## Out of scope`). Afterwards, search the file for `local_guard: text`; nothing is left.
- `images/gen.py` and `images/10_run_modes.svg`, in two moves:
  1. In `images/gen.py` 92-95, the guard rows become `["base guards"]`, `["base guards", "+ agentsh"]`, `["base guards"]`, `["base guards"]`. Regenerate into a temp folder (`python3 images/gen.py <tmp>`) and confirm `<tmp>/10_run_modes.svg` equals the committed file.
  2. Change the label on line 92 to `local_guard: none, the default`, regenerate into the temp folder again, and copy only `10_run_modes.svg` into `images/`. The SVG's diff is its line 9 alone. Don't run `gen.py` without the folder argument: it would rewrite all seven SVGs.

History stays as written: `docs/designs/`, `docs/projects.md`, `docs/open.md`, `specs/` and `app_docs/`. Leave `CLAUDE.md` and the phrase "text guard" where it names the text checks. The repo keeps no CHANGELOG.

For the documenter: older write-ups under `app_docs/` name `local_guard: text`. They are right for their date. The new write-up should say `text` is now the old name for `none`.

## Verification

Run from `.claude/skills/sssf/`, as the request says for a builder under agentsh:

- `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py` passes. The chain's verify phase runs the two left out. Never push to a bare repo in a test harness; fetch into it.
- `pytest tests/test_local_guard_names.py -q` passes, and fails if you put `"text"` back in `agentsh_guard.read` (try it once, then restore it).

Run from the repo root:

- `d=$(mktemp -d) && python3 images/gen.py "$d" && diff "$d/10_run_modes.svg" images/10_run_modes.svg` exits 0. It exits 1 at the baseline, because `gen.py` lags the SVG.
- agentsh is unchanged. Read `git diff 92c7e8d -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: it shows the nine lines in step 3 and no other. `git diff 92c7e8d --stat -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml .claude/skills/sssf/templates/adws/adw_modules/rules.py .claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts .claude/skills/sssf/scripts/` prints nothing.
- agentsh renders the same bytes. Unpack the baseline's modules into a temp folder (`git archive 92c7e8d .claude/skills/sssf/templates/adws | tar -x -C <tmp>`). Write a short script with your file tool, outside the repo, that takes a modules path, builds `RepoSettings(local_guard="agentsh", secret_files=["*.pem"], deny_exec=[...], network_allow=["api.example.com"])` and the shipped roster, calls `agentsh_guard.guard_for` with fixed paths, and prints the sha256 of `guard.model_dump_json()`, of `render_config(template, [...])` and of `cache_env("/x")`. Run it against the baseline copy and against the worktree. The three digests match. Put both outputs in your report. Don't commit the script.
- `git diff 92c7e8d --stat -- docs/designs docs/projects.md docs/open.md app_docs adws` prints nothing.

## For the engineer

- `GLOSSARY.md` line 34 is protected and outside the builder's `writes`. Suggested entry: "The optional layer around the base guards for runs on the host, chosen by `local_guard`. `none`, the default, adds nothing, so the base guards run alone; `agentsh` adds agentsh. `text` is the old name for `none` and still loads."
- The hub's own install under `adws/` still says `text` until `just hub-update` runs after the merge. The hub's `repo.yaml` says `agentsh`, so its runs are unaffected meanwhile.
- If the skill carries a version stamp that changes to `templates/adws/adw_modules/` bump, the builder follows what the last such commit did. I didn't find one in the time I had.

## Out of scope

Each item below is an old hole or gap. It holds at `92c7e8d` and after this change, under `none` and `text` alike. A reviewer files it as old.

Known limits of a run with no local guard, as `.claude/skills/sssf/references/config.md` states them. The change renames the value on these lines and leaves each limit standing:

- **Line 571: a script file's commands go unread.** The text rules read only the command line. Under the no-layer value, nothing reads what a script file runs when an agent writes one and runs it.
- **Line 587: a bad byte on pi's stdout ends the run.** Under the no-layer value SSSF reads the local pipe as text, so a byte that isn't valid UTF-8 stops pi and ends the run with an error. The same paragraph's other limit also stands: a command can write a forged event to the coding agent's stdout.
- **Line 608: the agent keeps the engineer's sign-in.** With the base guards alone, agents keep the engineer's sign-in and `~/.claude`.
- **Line 661: hard links.** Under agentsh, agents may still hardlink between workspace files, because the launch check allows those. The opt-out the line offers is the no-layer value, which runs no link check at all.

The rest:

- An agent can still edit `repo.yaml` so the next run has no local guard. `none` and `text` both do it now.
- Everything in "What the base guards alone leave open" in `docs/concepts.md`. The no-layer value is the same mode under a new name.
- `repo.yaml` written as `none` under modules older than this change refuses at load. That is fail closed, and it ends when the repo updates.
- The hub's install under `adws/`, and the projects' `repo.yaml` files. Each picks up the template at its next update; a customized copy keeps `text`, which keeps loading.
- Printing a run's guard layers at its start.
- Anything `agentsh` does, and the "text guard" wording where it names the text checks (`agentsh_guard.py` 3 and 354, `rules.py` 73, `CLAUDE.md` 17).
- `images/09_guards.svg`'s footer, which still says "text checks".
