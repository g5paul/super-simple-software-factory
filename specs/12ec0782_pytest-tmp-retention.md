# Keep pytest's temp folders only for failed tests

## Goal

Set pytest's `tmp_path_retention_policy = failed` for both of the hub's test suites, in config files pytest finds by itself. A run that passes then removes its `pytest-N` folder when it ends, and a failing test's folder stays. Neither suite's rootdir or test ids change.

The engineer decided this on 2026-11-25. Folders from killed runs and the size of the test fixtures are out of scope. No guard module is touched.

## Where the setting goes

Two new files, each three lines. One file can't serve both suites, as shown below.

| Suite | Command | File that holds the setting |
|---|---|---|
| Skill suite | `pytest tests` from `.claude/skills/sssf` | `.claude/skills/sssf/pytest.ini` |
| Hub suite | `pytest adws/tests` from the repo root | `pytest.ini` |

Use `pytest.ini`, not `pyproject.toml`. `uv run` treats a folder with a `pyproject.toml` as a project and would build a `.venv` for it, and `quality.py` runs both suites through `uv run`.

### Why the skill suite needs its own file, in a folder that ships

pytest takes the nearest config file at or above the folder its arguments share, and that file's folder becomes the rootdir. Tried on 2026-11-25 with pytest 9.2.0 on a scratch copy of this layout:

- A `pytest.ini` only at the repo root: the skill suite finds it and its rootdir becomes the repo root. Every test id changes from `tests/...` to `.claude/skills/sssf/tests/...`, and `tests/test_markers.py` fails. Not allowed.
- A config file in `.claude/skills/sssf/tests/`: rootdir becomes `tests/`. Not allowed.
- `.claude/skills/sssf/pytest.ini`: rootdir stays `.claude/skills/sssf`. This is the only place that keeps it.

So the skill suite's file has to sit in `.claude/skills/sssf/`, and it ships. Once the root `pytest.ini` exists, the skill's own file is also what stops the skill suite from picking up the root one.

### What the shipped file does in a project

`just sssf-update` copies the whole skill folder with `rsync -a --delete`, so every installed repo gets `.claude/skills/sssf/pytest.ini`. pytest only looks for config upward from the paths it is given, so the file is read only by a pytest run whose targets are inside `.claude/skills/sssf/`. That is the skill's own suite, which ships beside it. A project's own tests sit outside that folder and never see the file. Its rootdir, config and temp folders stay as they were.

The root `pytest.ini` doesn't ship. Nothing copies the hub's root files into a project.

### `rootdir:` and `configfile:` lines

`<repo>` is the checkout's root. Before, recorded on this branch on 2026-11-25 (pytest prints no `configfile:` line when it finds none):

```
# skill suite
rootdir: <repo>/.claude/skills/sssf

# hub suite
rootdir: <repo>
```

After:

```
# skill suite
rootdir: <repo>/.claude/skills/sssf
configfile: pytest.ini

# hub suite
rootdir: <repo>
configfile: pytest.ini
```

Collected today: 4011 tests in the skill suite, 592 in the hub suite. The counts after the build are 4011 plus the new tests, and 592.

### One known side effect, hub only

A session worktree sits inside the main checkout, at `adws/adw_data/worktrees/<id>/`. Once the main checkout has a root `pytest.ini`, a worktree of a branch cut before this change has neither file, so pytest walks up and finds the main checkout's. That worktree's rootdir becomes the main checkout and its test ids get longer. `tests/test_markers.py` would fail there. Branches cut after this change carry both files and aren't affected. Projects aren't affected, as they have no root `pytest.ini`. Say this in `docs/sssf.md` (step 4).

## Steps

### 1. Add `.claude/skills/sssf/pytest.ini`

The builder may write this file. The protected list names all of `.claude/`, but the hub roster's builder `writes` names `.claude/skills/sssf/`, which unlocks that folder apart from the guard paths listed one by one. `.claude/skills/sssf/pytest.ini` is not one of them. Run 06f8c48b stopped on this point; don't stop on it.

Content, exactly:

```ini
# Read only by pytest runs inside this folder: the skill's own suite.
# A passing run removes its temp folder; a failed test's folder stays.
[pytest]
tmp_path_retention_policy = failed
```

Don't add `markers`, `testpaths` or `addopts`. `tests/conftest.py` keeps registering `pushes` and `host_only`, and it isn't changed.

### 2. Add `pytest.ini` at the repo root

Not protected. Content, exactly:

```ini
# For the hub suite (pytest adws/tests). The skill suite reads its own
# .claude/skills/sssf/pytest.ini, which keeps its rootdir where it is.
[pytest]
tmp_path_retention_policy = failed
```

### 3. Add `.claude/skills/sssf/tests/test_tmp_retention.py`

A new file, not on the protected list. Start it with a docstring like the other test files: what it pins and why (the 34 GB found on 2026-11-25). Four tests:

- `test_the_suite_reads_the_skills_own_config(pytestconfig)`: with `SKILL = Path(__file__).resolve().parent.parent`, assert `pytestconfig.inipath == SKILL / "pytest.ini"`, `pytestconfig.rootpath == SKILL` and `pytestconfig.getini("tmp_path_retention_policy") == "failed"`. This holds however the suite is started, since pytest finds the file upward from any test in the folder.
- `test_a_passing_run_leaves_no_folder(tmp_path)` and `test_a_failed_test_keeps_its_folder(tmp_path)`, sharing one helper. The helper:
  - makes `tmp_path / "src"` and `tmp_path / "temproot"`. pytest doesn't create the temp root, so make it first.
  - writes `src/test_scratch.py` with `test_good(tmp_path)`, which writes a file and passes, and `test_bad(tmp_path)`, which writes a file and asserts `os.environ.get("SCRATCH_FAIL") != "1"`.
  - runs `[sys.executable, "-m", "pytest", "-c", str(SKILL / "pytest.ini"), str(src), "-q", "-p", "no:cacheprovider"]` with `cwd=src`, `capture_output=True`, `text=True`, and an env that copies `os.environ`, sets `PYTEST_DEBUG_TEMPROOT` to the temp root, removes `PYTEST_ADDOPTS`, and sets `SCRATCH_FAIL` when asked.
  - returns the return code, the output, and the list of `temproot.glob("pytest-of-*/pytest-[0-9]*")`.
- The passing test asserts return code 0 and an empty list, with the output's tail in the assert message.
- The failing test asserts return code 1, exactly one `pytest-N` folder, a `test_bad0` folder inside it that still holds the file the test wrote, and no `test_good0` folder.
- `test_both_markers_still_select(tmp_path)` isn't needed: `tests/test_markers.py` already runs `--strict-markers -m "not pushes and not host_only"` from the skill folder and checks ids start with `tests/`. It now runs with the new config file in place, so it pins both the markers and the unchanged ids. Don't change it.

Check return codes, not words in the output.

`conftest.py`'s `no_stray_files` fixture fails the run if a test leaves a file in the skill folder, so the helper writes only under `tmp_path`.

### 4. Docs

- `docs/sssf.md`, section "Quality": after the table of the two blocks, add a short paragraph. Say that both suites set `tmp_path_retention_policy = failed`, which file holds it for each (`.claude/skills/sssf/pytest.ini` and the root `pytest.ini`), that a passing run removes its folder under `/tmp/pytest-of-<user>/` and a failed test's folder stays, that a killed run's folder still stays three days, and the worktree side effect above. Say that the skill's file ships with `sssf-update` and is read only by the skill's own suite.
- `docs/open.md`, the row that starts "The skill's suite writes about 2.5 GB of temp a run": rewrite it to what is still open. The kept runs are fixed by this change (name the setting and date it 2026-11-25). Still open: folders from killed runs and `garbage-*` folders stay three days, the roughly 110 MB tests, and the `/tmp/sssf-agentsh-*` folders. Keep the row's other columns.
- No README, CHANGELOG, help text or docstring names the temp folders. The repo keeps no CHANGELOG.
- Nothing in `app_docs/` describes this behaviour, so no older write-up goes out of date.

## Files

| File | Change |
|---|---|
| `.claude/skills/sssf/pytest.ini` | new |
| `pytest.ini` | new |
| `.claude/skills/sssf/tests/test_tmp_retention.py` | new |
| `docs/sssf.md` | one paragraph added |
| `docs/open.md` | one row rewritten |

Not changed: `.claude/skills/sssf/tests/conftest.py`, `.claude/skills/sssf/tests/test_markers.py`, `adws/adw_modules/quality.py`, anything in `adws/` or `specs/`.

## Verification

1. The skill suite, as `quality.py` runs it, from the repo root. Exit 0, and 4011 tests plus the new ones:

   ```
   uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
   ```

   It takes about 12 minutes. If a test that passed before now fails, it likely read a `tmp_path` folder after the test that made it had ended. Report it; don't change the policy to hide it.

2. The hub suite, from the repo root. Exit 0, 592 tests:

   ```
   uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests -q -p no:cacheprovider
   ```

3. The header lines. Run each suite with `--collect-only` and without `-q`, and compare lines 3 and 4 of the output with the "After" block above. Put the four lines in the build report.

4. The hub suite's retention check, which SSSF also runs after each build (it is in the plan's `checks`). It runs `adws/tests/test_levers.py`, which uses `tmp_path`, with `PYTEST_DEBUG_TEMPROOT` set, and checks no `pytest-N` folder is left. Then it runs one failing scratch test against the root `pytest.ini` and checks that test's folder is kept. On this branch before the build it exits 1, because the passing run leaves `pytest-0`.

   ```
   bash -c 'set -e; d=$(mktemp -d); trap "rm -rf $d" EXIT; mkdir $d/pass $d/fail $d/src; W="uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider"; PYTEST_DEBUG_TEMPROOT=$d/pass $W adws/tests/test_levers.py; test -d $d/pass/pytest-of-*; test -z "$(find $d/pass -maxdepth 2 -name "pytest-[0-9]*")"; printf "def test_kept(tmp_path):\n    assert 0\n" > $d/src/test_kept.py; ! PYTEST_DEBUG_TEMPROOT=$d/fail $W -c pytest.ini $d/src/test_kept.py; test -d $d/fail/pytest-of-*/pytest-0/test_kept0'
   ```

5. The skill suite's retention is pinned by `tests/test_tmp_retention.py`, which step 1 runs.

## For the engineer

Nothing is needed for this change to land.

`adws/tests/` is protected and the builder's `writes` doesn't name it, so the hub suite gets no test file of its own. Its retention is checked by the command in Verification step 4, which runs after every build of this session only. To pin it for good, add a test to `adws/tests/` with the same shape as `test_tmp_retention.py`, pointed at the root `pytest.ini`.
