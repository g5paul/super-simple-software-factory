# Refuse a `repo.yaml` that isn't a mapping

Item one of `docs/open.md`'s P3 row "Leftovers from guard run 7bad1e2b's reviews". The brief is `specs/tasks/guard-repo-yaml-mapping.md`.

## The problem

`repo_settings.load` (`.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py:177`) ends with `RepoSettings.model_validate(data or {})`. Any falsy top-level document becomes `{}`, so a `repo.yaml` holding `[]`, `false`, `0` or `''` loads as all defaults with no error. A repo on `local_guard: agentsh` drops to no layer, and `sandbox`, `secret_files`, `deny_exec`, `network_allow`, `agent_rules` and the cost caps reset with it. A truthy non-mapping already refuses, because pydantic rejects it, but its message names neither the file nor what the top level must be.

## Threat model

The agent runs as the engineer's own user, so it can write any file that user can, `repo.yaml` included. What holds it:

- `repo.yaml` is under `adws/adw_sssf_config/`, a protected path and a factory file. `permissions.enforce` rolls a write back and stops the run, but only after the phase's sends are done.
- `local_guard` is read once, at `session.ensure` (`agentsh_guard.read`), before any agent starts. The other settings are read again later: `agents.execute` loads the file at each agent start, and `budget.py` loads it before each send.

What this guard can't stop at its level:

- An agent that can write the file can write a valid mapping with weaker settings (`local_guard: none`, `agent_rules: []`). The non-mapping gives it no extra power, which is why the 7bad1e2b reviewer rated it Low.
- An agent can delete the file, and a missing file gives the defaults by design.

So the change is for the engineer as much as against the agent: a file that loses every setting must say so. After it, a non-mapping written mid-run makes the next `load` raise, and the run stops where it went on with defaults before.

## Baseline

`main` at `4d9370b`. Every finding is judged against it. Probed there, by writing each body to `adws/adw_sssf_config/repo.yaml` in a temp folder and calling `repo_settings.load`:

| Top-level document | `safe_load` gives | `load` at `4d9370b` |
|---|---|---|
| `[]`, `false`, `no`, `0`, `0.0`, `''`, `""`, `!!set {}`, `!!binary ""` | falsy non-mapping | **all defaults, no error** (the hole) |
| `"agentsh"`, `agentsh`, `[local_guard: agentsh]`, `- local_guard: agentsh`, `1`, `true`, `2026-11-15` | truthy non-mapping | `ValidationError`: "Input should be a valid dictionary or instance of RepoSettings" |
| no file, zero bytes, `\n`, a comment only, `null`, `~`, `Null`, `NULL`, `---`, `--- ~`, a lone BOM | `None` | all defaults |
| `{}` | empty `dict` | all defaults |
| `local_guard: agentsh` | `dict` | loads, `local_guard == "agentsh"` |
| `locl_guard: agentsh`, `local_guard: None`, `1: 2` | `dict` | `ValidationError` |
| two documents (`a: 1`, `---`, `b: 2`) | error | `yaml.YAMLError` |
| a directory at the path | not read | all defaults |

`ValidationError` is a subclass of `ValueError` (checked on the suite's pydantic).

The template `templates/repo.yaml` and the hub's own `adws/adw_sssf_config/repo.yaml` both parse to a `dict`.

## Bar

Must do:

- **Fail closed.** `load` raises for every top-level document that is neither `None` nor a `dict`. At least: `[]`, `false`, `0`, `''`, `"agentsh"`, `[local_guard: agentsh]`, `1`, `true`.
- **Useful message.** The error names the file's path and says the top level must be a mapping of settings.
- **Reach the engineer as today.** `agentsh_guard.read` turns it into `SystemExit("adws/adw_sssf_config/repo.yaml is invalid: …")`, the same way it does a validation error.

Must never do:

- Change what any `None` or `dict` document loads to. A missing file, an empty file, a comments-only file and an explicit `null` give `RepoSettings()` as on the baseline. Every mapping gives the same settings or the same `ValidationError`.
- Keep an agent's file, run an agent's code, or delete or overwrite an engineer's file where the baseline doesn't. `load` only reads, and this change adds no write.

## The change

### 1. `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

Replace the body of `load` (lines 177-180). Nothing else in the file changes.

```python
def load(root: Path) -> RepoSettings:
    """This repo's settings. A missing file gives the defaults, and so does one whose
    document is empty: no content, only comments, or null. Any other top level than a
    mapping refuses. `[]`, `false`, `0` and `''` must never load as "no settings"."""
    path = root / PATH
    data = yaml.safe_load(path.read_text()) if path.is_file() else None
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: the top level must be a mapping of settings "
                         f"(`key: value` lines), not a {type(data).__name__}")
    return RepoSettings.model_validate(data)
```

Why this is the whole change:

- **Mappings are untouched by construction.** For a `dict`, the baseline passes `data or {}` to `model_validate`: the same non-empty dict, or `{}` for an empty one. The new code passes `data`, which is the same value in both cases. So every mapping loads to the same settings or the same error, whatever its body. That covers the hub's file, the template and every body an earlier harness loaded.
- **`ValueError`, not a new class.** `ValidationError` is a `ValueError`. The two places that turn a load failure into a message, `agentsh_guard.read` (`agentsh_guard.py:464`) and `scripts/agentsh_install.py:118`, both catch `ValueError` by name. No handler in the skill or in `adws/` catches `ValidationError` alone, so the new error goes everywhere the old one went. Neither protected file needs a change.
- **The type name, not the value.** The message says `list`, `bool`, `int` or `str`. It doesn't echo the document.
- A truthy non-mapping now raises this `ValueError`, not pydantic's `ValidationError`. It refused before and refuses now, with the better message.

### 2. `.claude/skills/sssf/tests/test_repo_yaml_mapping.py` (new)

Follow `.claude/skills/sssf/tests/test_local_guard_names.py`: the same `SKILL` path setup, `SHIPPED = SKILL / "templates" / "sssf.config.yaml"`, a `cfg` fixture from `agents.load_config(str(SHIPPED))`, and a `_repo_with(tmp_path, body)` helper that runs `git init` and writes the body to `adws/adw_sssf_config/repo.yaml`. No test-only roster. No `pushes` or `host_only` marker: the tests only write under `tmp_path`.

One parametrized case per body, so each is pinned by name:

- **Refused.** Bodies: `[]`, `false`, `0`, `''`, `"agentsh"`, `[local_guard: agentsh]`, `1`, `true` (the bar's eight), then `agentsh`, `""`, `0.0`, `no`, `- local_guard: agentsh`, `!!set {}`, `!!binary ""`, `2026-11-15`. Each with a trailing newline. For each:
  - `repo_settings.load(repo)` raises `ValueError`, and the message holds `str(repo / repo_settings.PATH)`, the word `mapping`, and the document's type name (`list`, `bool`, `int`, `str`, `float`, `set`, `bytes`, `date`).
  - After `monkeypatch.chdir(repo)`, `agentsh_guard.read(cfg)` raises `SystemExit` whose text holds `repo.yaml` and `mapping`.
- **Kept as defaults.** No file at all, then bodies: zero bytes, `\n`, `# only a comment\n`, `null\n`, `~\n`, `Null\n`, `NULL\n`, `---\n`, `--- ~\n`, `﻿`, `{}\n`. For each: `repo_settings.load(repo) == repo_settings.RepoSettings()`, and `agentsh_guard.read(cfg) == LocalGuard(mode="none")` from inside the repo.
- **Mappings load as before.** For each body, `repo_settings.load(repo) == RepoSettings.model_validate(yaml.safe_load(body))`. The right side doesn't go through `load`, so it is the baseline's answer. Bodies: `local_guard: agentsh\n` (also assert `local_guard == "agentsh"`), `local_guard: none\n`, `local_guard: text\n`, `deny_exec: []\n`, `local_guard: agentsh\nlocal_guard: none\n`, `local_guard: none\nlocal_guard: agentsh\n`, `local_guard: !!str none\n`, `local_guard: none\nsandbox: vercel\n`, `merge: ff_only\nmax_worktree_runs: 1\n`, the text of `SKILL / "templates" / "repo.yaml"`, and the text of the hub's `SKILL.parents[2] / "adws/adw_sssf_config/repo.yaml"` when that file exists.
- **Mapping errors keep their type.** `locl_guard: agentsh\n`, `local_guard: None\n`, `1: 2\n` and `local_guard: agentsh\nsandbox: vercel\n` raise `pydantic.ValidationError`. Two documents (`a: 1\n---\nb: 2\n`) raise `yaml.YAMLError`.
- **The story.** A repo with `local_guard: agentsh\n` loads `agentsh`. Rewrite the file as `[]\n` and `load` raises. It does not return a `RepoSettings`.

Every refused case for `[]`, `false`, `0`, `''` fails on the baseline, where `load` returns defaults. The other refused cases fail there on the message check.

### 3. `.claude/skills/sssf/references/config.md`

In "Repo settings in `repo.yaml`" (line 515), after the sentence ending "so a misspelled one fails the run.", add:

> It also refuses a file whose top level isn't a mapping: a list, a string, a number or a boolean, `[]`, `false`, `0` and `''` included. A missing file, an empty one, one with only comments, and an explicit `null` or `~` all give the defaults.

### Docs checked and left alone

- The repo keeps no CHANGELOG, and has no `app_docs/`, so no older write-up goes stale.
- `templates/repo.yaml`'s comments describe keys, not the top level. No change.
- `README.md`, `docs/sssf.md` and `SKILL.md` don't describe how the file is validated.
- `docs/open.md` is the engineer's record. See "For the engineer".

## The other loaders

- **`agents.load_config` (`.claude/skills/sssf/templates/adws/adw_modules/agents.py:50`): already refuses, left as it is.** This is from reading the code; `agents` can't be imported inside this run's sandbox (see "For the engineer").
  - A truthy non-mapping (`agentsh`, `[a]`, `1`, `true`) raises `AttributeError` at `raw.get("defaults", {})` on the next line.
  - A falsy one (`[]`, `false`, `0`, `''`) becomes `SSSFConfig()`, the same as an empty roster: the default settings and **no agents**. No agent can start from it. `agents.validate` exits with "agent … is not defined in the config", and `agents.execute` calls `resolve` on its first line, which exits the same way.
  - Commands that take no agent (`adw_kill.py`, `adw_worktree.py`, `adw_quality.py`, `adw_sandbox_usage.py`, `adw_export.py`) run on the defaults, exactly as they do for an empty roster. `adw_export.py:133` uses `SSSFConfig()` for a missing roster on purpose.
  - So nothing agent-shaped runs on a non-mapping roster. The messages are poor; that is listed under "Out of scope".
- **`budget.py:231`** reads SSSF's own `machine.yaml` and **`scripts/agentsh_install.py:134`** reads agentsh's installed config. Neither is the engineer's repo config, and I found no path that lets an agent in the repo's tree write them that the brief doesn't already rule out. Left alone.

## Out of scope

Old holes this change leaves open, on baseline and head alike:

- **Something at the path that isn't a regular file.** A directory, a dangling symlink, a FIFO or a socket at `adws/adw_sssf_config/repo.yaml` fails `path.is_file()` and loads as defaults. The brief keeps "a missing file gives the defaults", and this rides on the same test.
- **A deleted file** loads as defaults, by design.
- **A valid mapping with weaker settings** loads. Who may write `repo.yaml` is unchanged.
- **A non-mapping `sssf.config.yaml`** refuses with a traceback (`AttributeError`) or with "agent … is not defined … available: []", not with a message naming the file. Agentless commands run on defaults.
- What a mapping may contain, the `local_guard` values, and the other items in the 7bad1e2b leftovers row.

## Verification

1. The new test file passes. SSSF runs this after the build, from the repo root:

   ```
   uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_repo_yaml_mapping.py -q -p no:cacheprovider
   ```

2. The repo's own `test` check, the whole skill suite, passes with no test removed or edited. `tests/test_local_guard_names.py` in particular: its `_stale` check allows `repo_settings.py` exactly one `"text"` comparison, and this change adds none.
3. The diff against `4d9370b` touches only the three files above, besides the spec. `agents.py`, `agentsh_guard.py` and `scripts/agentsh_install.py` are unchanged.
4. A builder inside the agentsh sandbox can't collect the suite (see "For the engineer"), but can check `load` directly, because `repo_settings` imports nothing that reads `.env`. From `.claude/skills/sssf`, run `uv run --with pydantic --with pyyaml python -c '…'` with a script that puts `templates/adws` on `sys.path`, imports only `adw_modules.repo_settings`, writes a body under a temp folder and calls `load`.

## For the engineer

- **The hub runs its stamped copy.** `adws/adw_modules/repo_settings.py` is identical to the template at `4d9370b` and is protected, so the builder doesn't touch it. The hub's own runs, on `local_guard: agentsh`, keep the old `load` until you run `just sssf-update` after the merge. Other projects get it on their next update.
- **`docs/open.md`.** Strike item one from the P3 row "Leftovers from guard run 7bad1e2b's reviews" once this lands. If you want them filed, the row can gain the two old holes above: a non-regular file at the path, and the roster's poor message.
- **Protection.** `repo_settings.py` and the new `tests/test_repo_yaml_mapping.py` are not in `protected_files` or in `guard_builder`'s `writes` in `adws/adw_sssf_config/sssf.config.yaml`, so the ordinary builder can change both. Add them to both lists if they should be held like the other guard files.
- **The suite doesn't collect inside this run's sandbox.** `utils.py:16` calls `load_dotenv()`, which walks up from the worktree to the main checkout's `.env`. The secret-file guard denies the read, and every test file that imports `adw_modules.utils` stops at collection with `PermissionError` (seen with `tests/test_local_guard_names.py` and `tests/test_silence.py`, exit 2). I left the guard alone. It means the builder can't run these tests itself in a worktree run, and the check above only proves the change when SSSF runs it outside the sandbox.
- **The 62 bodies.** The harness I found is `adws/adw_data/sessions/7bad1e2b/context_handoff/bypass_review_1/probe.py` in the main checkout, untracked, with 43 bodies; I didn't find a 62-body list. Three of its bodies (`false`, `[]`, `0`) are the ones this change turns into refusals. The rest that loaded are mappings or empty, which the change can't alter, for the reason given under step 1.
