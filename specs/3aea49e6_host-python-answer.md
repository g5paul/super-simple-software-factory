# Check the host Python's answer before the OpenShell image uses it

`sandbox_remote._host_python` returns its interpreter's stdout as is. This change closes gap 2 in `docs/open.md`: the interpreter runs isolated, the function returns a version or `""`, and OpenShell refuses a bad answer in one plain line. Gap 1 stays open by the engineer's decision of 2026-11-08.

## Threat model

An agent runs as the engineer's own user in local mode (`--sandbox none`), so it can write anything that user can, including uv's managed-Python folder and any folder on `PATH`. This guard can't stop that at its level. In a VM run (`--sandbox openshell`, `vercel`, `e2b`), an agent reaches the host only through what comes back: commits, bundles and output, and what the host runs on them.

The route this code touches: `uv python find` runs in the tree, and the host then runs whatever interpreter it names. A `.venv/bin/python` committed on a run branch can be that interpreter on a later `Remote.open` in the worktree. That is an old hole (see Out of scope). This change doesn't close it, and must not widen it. The interpreter's stdout is therefore untrusted text: it may hold newlines, escape codes, bytes that don't decode, or megabytes.

## Baseline

`main` at `8f1be02`. Every finding is judged against that commit. `sandbox_remote.py`, `sandbox_openshell.py` and `scripts/openshell_image.py` are the same at this branch's head, 45c2bfc.

At the baseline:

- `_host_python` returns stdout unchecked.
- `containerfile` raises `ValueError` on a bad value. In `require_sdk`, `_create` and the script that raise happens in `image_tag`/`containerfile`, outside their `try` blocks, so it is a traceback.
- `Remote.open` passes the value on as `UV_PYTHON` and logs it in the `sandbox` event.

## Bar

Must do:

- The interpreter runs with `-I -S`, so no `sitecustomize`, `.pth` file or `PYTHON*` variable runs or prints.
- `_host_python` returns a string that fully matches `\d+\.\d+` under `re.ASCII`, or `""`, and doesn't raise on any interpreter output.
- A bad answer stops OpenShell with one plain line naming the interpreter and what it printed: `SandboxFailed` in the run, exit 2 from the script, `SystemExit` from `require_sdk`. No traceback.
- A host with no uv or no Python keeps today's behaviour. Vercel and E2B treat a bad answer as no `UV_PYTHON`.

Must never, where the baseline does otherwise:

- Run anything the baseline wouldn't. The `uv python find` call stays byte for byte: same argv, same inherited environment, same `cwd=tree`, `capture_output=True`, `text=True`, `timeout=60`. No new call to uv, and no extra run of the interpreter. `Remote.open` and `_create` each still make their one call to `_host_python`.
- Keep an agent file, or delete or overwrite an engineer's file. This change writes no files.
- Let the interpreter's output reach a message raw. It goes in as `repr` of a cut string, so it stays one line with no live escape codes.

Sort anything that runs here and not at the baseline by decision 14 in `docs/designs/openshell-sandbox.md`: reachable from a VM run is a bypass, and reachable only by a local agent writing a file outside the repo's tree is a local-mode limit the spec names and `docs/open.md` lists.

**Local-mode limits this spec names: none.** `-I -S` only removes startup paths. A reviewer should weigh one edge: `-I` also ignores a `PYTHONHOME` the engineer set, so the interpreter finds its standard library from its own location. Anything that could plant a file there could replace the interpreter itself, which the baseline already runs, so this is not a new route.

## Out of scope

Reviewers should file these as old holes:

- Gap 1: the caller's environment picks the Python, so the setup script and a run can key different image tags. Also any change to how `uv python find` is called or to `UV_PYTHON`. `--system` stays dropped.
- `docs/open.md`'s next row: `UV_PYTHON` comes from the worktree while the image's Python comes from the main checkout, and the host runs a `.venv/bin/python` committed on the run branch. After this change that file still runs, with `-I -S`, as it does at the baseline.
- Any local agent replacing the interpreter, or a file on `PATH` or in uv's managed folder. Decision 14 covers these.
- The network policy, building an image, and running a sandbox.

## Changes

All paths are from the repo root.

### `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

Add two module constants near `TOKEN_FILE`: `MAJOR_MINOR = re.compile(r"\d+\.\d+", re.ASCII)`, and `ANSWER_MAX = 200` for how much of a bad answer a message keeps.

Change `_host_python` to the shape below. The second parameter is optional, so `_host_python(tree)` still returns a plain string for the existing test.

```python
def _host_python(tree: Path, problems: list[str] | None = None) -> str:
    problem = ""
    try:
        found = subprocess.run(["uv", "python", "find"], cwd=tree, capture_output=True,
                               text=True, timeout=60)          # unchanged
        if found.returncode != 0:
            return ""
        interpreter = found.stdout.strip()
        version = subprocess.run([interpreter, "-I", "-S", "-c",
                                  "import sys; print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    except ValueError as error:     # uv's stdout didn't decode, or held a NUL
        problem = f"`uv python find` printed no usable path ({str(error)[:ANSWER_MAX]!r})"
    else:
        if version.returncode != 0:
            return ""
        answer = version.stdout.strip()
        if MAJOR_MINOR.fullmatch(answer):
            return answer
        problem = (f"the interpreter uv picks, {interpreter[:ANSWER_MAX]!r}, printed "
                   f"{answer[:ANSWER_MAX]!r}, not a Python major.minor")
    if problems is not None:
        problems.append(problem)
    return ""
```

Rules behind this shape:

- **No trailing period on a problem.** `require_sdk` appends `. See ...` to it.
- **`errors="replace"`** goes on the interpreter call only, so bytes that don't decode become a bad answer and not a `UnicodeDecodeError`.
- **Exit 0 with empty stdout is a bad answer.** The interpreter ran and gave no version.
- **Still "no Python", with no problem:** a non-zero exit, a timeout, or an `OSError` from either call. That includes uv printing an empty path. This is the baseline's behaviour.

Update the docstring: `""` for no uv or no Python, and `""` with one line added to `problems` for an answer that isn't a version. Say the interpreter runs with `-I -S` so nothing at its startup can print.

In `Remote.__init__`, add `self.python_problem = ""` beside `self.python = ""`.

In `Remote.open`, keep one call:

```python
problems: list[str] = []
self.python = _host_python(self.tree, problems)
self.python_problem = problems[0] if problems else ""
```

The base class does nothing else with `python_problem`, so Vercel and E2B run on with no `UV_PYTHON`, through the existing `_env` line.

### `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

`image_python` raises on a bad answer:

```python
problems: list[str] = []
python = sandbox_remote._host_python(repo_root(path), problems)
if problems:
    raise ValueError(problems[0])
return python
```

Update its docstring to say so.

All three callers already wrap `image_python` in `except (ValueError, ...)` and stop plainly: `require_sdk` with `SystemExit`, `_create` with `OpenShellError`, which `Remote.open` turns into `SandboxFailed`, and the script with exit 2. So **`require_sdk`'s and the script's code don't change**, and `containerfile`'s own check stays as it is.

`_create` also needs the worktree's answer. `Remote.open` reads the worktree and `image_python` reads the main checkout, so a bad answer in the worktree alone would otherwise pass silently with no `UV_PYTHON`. Add this after the name-length check, before `folder_for`:

```python
if self.python_problem:
    raise OpenShellError(self.python_problem)
```

### `.claude/skills/sssf/scripts/openshell_image.py`

No change. The tests below prove the refusal.

### `.claude/skills/sssf/tests/test_host_python.py` (new)

Use the existing conventions: the "Run from the skill root" docstring, and `sys.path.insert` of `templates/adws`.

Helper `host(tmp_path, monkeypatch, answer: bytes)` builds a `bin` folder first on `PATH`, with the real `PATH` after it:

- a stand-in `uv` that logs `$PWD` and its args, then prints the interpreter's path;
- a stand-in interpreter (`#!/bin/sh`) that logs `$*` and `cat`s a file holding `answer`, so raw bytes work;
- stand-ins `openshell` and `podman` that log their calls and exit 0.

Repos come from a `git init` under a temp `HOME`, with the config copied from `templates/sssf.config.yaml`, as `write_shipped_config` does in `test_sandbox_openshell.py`. Never use a test-only config.

Tests:

1. **Bad answers give `""` and one problem.** Parametrize: `3.13\nhello from sitecustomize`, `hello\n3.13`, `3.13.1`, `3`, `٣.١٣` (non-ASCII digits), `3.13; curl x|sh`, `\xff\xfe`, and empty. Each gives `_host_python(tree) == ""` with no exception. With a list passed, there is one problem, on one line, naming the interpreter's path. `_host_python(tree)` without a list also gives `""`.
2. **A long answer is cut.** With 100,000 characters, the problem is under 1,000 characters.
3. **An escape code stays inert.** An answer holding `\x1b[2J` puts no raw ESC in the problem.
4. **A good answer passes.** `3.13\n` gives `"3.13"` and no problem.
5. **No Python gives no problem.** No `uv` on `PATH`, `uv` exiting 1, `uv` printing nothing, a missing interpreter path, and an interpreter exiting 1 each give `""` with `problems == []`. For the same cases `osh.image_python(repo) == ""`, so the image has no Python, as today.
6. **Bad output from uv itself.** A stand-in `uv` printing `\xff\n`, and one printing `a\0b\n`, each give `""` and one problem, with no exception.
7. **The interpreter's argv.** The stand-in's log starts `-I -S -c `.
8. **Startup code no longer reaches the answer, against real Python.** Parametrize three routes:
   - `sitecustomize.py` in a folder named by `PYTHONPATH`, with `sys.executable` as the interpreter;
   - `sitecustomize.py` in the `site-packages` of a venv made with `sys.executable -m venv --without-pip`;
   - a `.pth` file with an `import` line in that venv's `site-packages`.

   Each file prints `hello`. A control runs the interpreter plainly and asserts `hello` is in its stdout, so the test can't pass for the wrong reason. The stand-in `uv` then names that interpreter, and `_host_python` gives the real `major.minor` with no problem.
9. **`uv python find` is unchanged.** A spy on `subprocess.run` records the first call: argv `["uv", "python", "find"]`, and kwargs exactly `{cwd: tree, capture_output: True, text: True, timeout: 60}`, with no `env`. Exactly two `subprocess.run` calls happen.
10. **`require_sdk`.** It raises `SystemExit` whose text is one line, naming the interpreter and the printed text in `repr`. The log shows no `podman` call.
11. **`_create` and `open`, main checkout bad.** `_create()` raises `OpenShellError` with the text. `box.open()` raises `SandboxFailed` with the text. Set `sandbox_remote.TOKEN_FILE` to a temp token file and delete `ANTHROPIC_API_KEY`. The log shows no `sandbox create`.
12. **`open`, worktree bad and main checkout good.** Use a real `git worktree add` under `adws/adw_data/worktrees/`, and a stand-in `uv` that picks its interpreter by `$PWD`. A control shows `osh.image_python(repo) == "3.13"`. `box.open()` from the worktree raises `SandboxFailed`, and no `sandbox create` happens.
13. **Script.** Run `sys.executable -I SCRIPT --repo repo --print`, and again without `--print`, with `SSSF_ADW_ID` removed from the env. Each exits 2, with empty stdout, one stderr line starting `openshell_image:` that names the interpreter and the printed text, no `Traceback`, and no `podman` call.
14. **A base `Remote` runs on.** A plain `Remote` subclass whose `_create` raises a sentinel exception shows `open` reaches `_create` with `python == ""` on a bad answer.

### `.claude/skills/sssf/tests/test_sandbox_remote.py`

Add one test and change no existing one: `test_a_bad_answer_means_no_uv_python(box, tmp_path, monkeypatch)`, which runs on both `box` params, Vercel and E2B. With a stand-in `uv` and interpreter first on `PATH`, set `box.python = sandbox_remote._host_python(box.host_tree)`. Assert it is `""`, and that `UV_PYTHON` is absent from `box.run(["sh", "-c", "env"]).stdout`.

### `docs/open.md`

Replace the "Two gaps in the OpenShell image's Python" row's Item text with the following, keeping its Kind, Since and Background cells:

> A gap in the OpenShell image's Python that guard run 609ad1c5's review found. With no `.python-version`, `uv python find` returns the calling `uv run` environment's Python, so `just openshell-setup` and a run could key different image tags and the run would refuse a built image; in sssf-sandbox both give 3.13 today. It fails closed. `uv python find --system` was tried in guard runs 6ff4f245 and e3308ebb and dropped, because it reaches uv's managed-Python folder and further along `PATH`, so a fix needs another design

Leave the next row, "Two older gaps", as it is.

### `docs/designs/openshell-sandbox.md`

Add this sentence to the "As built" bullet **The image's Python**, after the sentence that ends `(805664d, guard run 609ad1c5).`:

> The host runs that interpreter with `-I -S`, and an answer that isn't a major.minor refuses, naming the interpreter and what it printed.

## Docs this makes out of date

- `docs/open.md` and `docs/designs/openshell-sandbox.md`, as above.
- The docstrings of `_host_python` and `image_python`, as above.
- No change needed:
  - `.claude/skills/sssf/references/config.md` stays true. Its OpenShell section says which Python is picked, not how the answer is read.
  - No help text or README usage changes.
  - The repo keeps no CHANGELOG.
  - This repo has no `app_docs/`, so no older write-up needs a note.

## Verification

From the skill root, with the suite's own runner, which is also the check SSSF runs after each build:

```
cd .claude/skills/sssf && env -u SSSF_REFUSE_RULES uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_host_python.py tests/test_sandbox_remote.py tests/test_sandbox_openshell.py tests/test_openshell_image.py tests/test_openshell_setup_refuses.py -q
```

Keep the `env -u SSSF_REFUSE_RULES`. Without it, `test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules[vercel]` and `[e2b]` fail inside any agent that has the variable set, on unchanged code: the test copies `os.environ`, so the agent's own rules turn on the path it expects to be off. With the variable cleared, both pass. This change doesn't touch that test or cause the failure.

The builder should also confirm these by hand:

- `git diff` shows the `uv python find` lines unchanged.
- No existing test was edited. The only edit to an existing test file is the one test added to `test_sandbox_remote.py`.
- No file under `adws/adw_modules/` or `adws/tests/` changed.

## For the engineer

Nothing here blocks the build, since the tests import the skill's templates. After merging, the hub's own install in `adws/adw_modules/` is a protected copy and still holds the old code until `sssf-update` runs, the usual rollout.
