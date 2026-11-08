# Ship the repo's Python in the OpenShell image

## Summary

Proof run 62d231db ran every agent under `--sandbox openshell` to the end, then
failed its checks. Each VM command gets `UV_PYTHON` from
`sandbox_remote._host_python` (3.13 there), but the image only has Ubuntu's
Python 3.12. The policy blocks `releases.astral.sh` and GitHub, so
`uv run pytest` couldn't download 3.13.

The fix: the image installs the repo's Python with uv at build time, when the
build has network. The version is part of the Containerfile, so it is part of
the image tag. A run whose Python changed since the last build then fails in
`require_sdk` with the `just openshell-setup` message, as a missing image does
today.

All code changes are in the skill's template copy,
`.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`, and in
`.claude/skills/sssf/scripts/openshell_image.py`. Tests go in
`.claude/skills/sssf/tests/test_sandbox_openshell.py`.

## Threat model

- Agents run inside the OpenShell microVM as `ubuntu`. Host-side code
  (`require_sdk`, `_create`, `scripts/openshell_image.py`, `just openshell-setup`)
  runs as the engineer's own user. The image build runs under Podman as that
  user, with network.
- The Python version comes from the host's `uv python find` in the repo's main
  checkout, plus running the interpreter it finds with `-c "...'%d.%d'..."`
  (`sandbox_remote._host_python`). That reads repo files (`.python-version`,
  `pyproject.toml` `requires-python`, a project `.venv`). An agent's file only
  reaches the main checkout through the engineer's merge.
- What this guard can't stop at its level: the engineer's user can run anything.
  Once the engineer merges an agent's change, the host trusts it. That includes
  a committed `.venv/bin/python` that uv would pick. The baseline already runs
  that interpreter in the main checkout: every `just` recipe is `uv run
  adws/...` from the root, and `open()` runs `_host_python` in the worktree.
- The new risk is the version string reaching the Containerfile, which Podman
  runs as shell `RUN` lines. `containerfile` must accept only `^\d+\.\d+$` (or
  `""`) and raise `ValueError` for anything else, so no text from the host
  lookup can add a shell command to the build.

## Baseline

`main` at `7813dbf` ("Start VM agents with setsid -w, so OpenShell keeps their
stream"). Judge every finding against it. At the baseline:

- `containerfile(folder)` installs no Python beyond apt's `python3` (3.12).
- `image_tag(folder)` hashes only that text. For `Path("/home/u/repo")` it is
  `localhost/sssf-openshell:c1f8d90c5594`, computed from the baseline code.
- `require_sdk` and `_create` never look up a Python. `Remote.open` sets
  `self.python = _host_python(self.tree)` (the worktree) and passes it as
  `UV_PYTHON`.

## Bar

The change must:

- Install the version the host's uv picks in the repo's main checkout into the
  image, under `/usr`, as a `python3.<minor>` on `PATH`. The sandbox user's uv
  must find it with no environment variable of its own.
- End the build with a `RUN` line that fails unless `uv python find <version>`
  succeeds.
- Derive the version from one function, `image_python`, in all three callers
  (`require_sdk`, `_create`, `scripts/openshell_image.py`), so all three agree
  on the tag.
- Make `require_sdk` refuse, naming `just openshell-setup`, when the image for
  the current host Python isn't built.
- With no host Python (`""`), produce exactly the baseline Containerfile and tag.

It must never:

- Put text into the Containerfile that isn't `\d+\.\d+`.
- Open any network rule, or change `policy()` or `READ_ONLY_PATHS`.
- Keep an agent file, run agent code the baseline doesn't run, or delete or
  overwrite an engineer file.
- Build an image or create a sandbox from code or tests.
- Change any existing test.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

Read the whole file first.

- Next to `from .sandbox_remote import Remote`, add `from . import sandbox_remote`.
  Call `sandbox_remote._host_python` through the module so tests can patch it.
- New constants near `NODE`:
  ```python
  PYTHON_DIR = "/usr/local/share/uv/python"   # under /usr, which the policy reads
  PYTHON_BIN = "/usr/local/bin"               # on PATH, so uv finds python3.<minor>
  ```
- New function in the "layout" section, after `folder_for`:
  ```python
  def image_python(path: Path) -> str:
      """The Python, as major.minor, the image holds for the repo `path` is in:
      what the host's uv picks in its main checkout, the value a run passes as
      UV_PYTHON (sandbox_remote._host_python). "" when the host has none."""
      return sandbox_remote._host_python(repo_root(path))
  ```
  It doesn't catch errors. `repo_root` raises the same errors `folder_for`
  already raises, and callers catch them together.
- `containerfile(folder: Path, python: str | None = None) -> str`:
  - `None` means `image_python(folder)`. With the default layout the folder is
    the main checkout, so this matches what the callers pass. This default keeps
    the existing one-argument calls in the tests working
    (`test_the_script_and_provider_agree_on_the_tag` and
    `test_create_writes_the_policy_and_passes_no_upload` call
    `image_tag(folder)`).
  - If `python` is non-empty and not `re.fullmatch(r"\d+\.\d+", python)`, raise
    `ValueError(f"not a Python major.minor: {python!r}")`.
  - When `python` is non-empty, insert this line right after the uv install
    `RUN` line and before `npm install`:
    `f"RUN UV_PYTHON_INSTALL_DIR={PYTHON_DIR} UV_PYTHON_BIN_DIR={PYTHON_BIN} uv python install {python}"`.
    `uv python install` puts a `python3.<minor>` executable in the bin dir by
    default (`--help`: "only a `python{major}.{minor}` executable is
    installed"). `--install-dir` has the env form `UV_PYTHON_INSTALL_DIR`.
    `UV_PYTHON_BIN_DIR` sets the bin dir (`uv python dir --bin`).
  - The last `RUN` line stays `RUN node --version && uv --version && claude
    --version && git --version`. When `python` is non-empty, append
    `f" && uv python find {python}"`.
  - When `python == ""`, the returned text must equal the baseline text byte for
    byte. Only add lines conditionally; don't reorder or reword the existing
    ones.
- `image_tag(folder: Path, python: str | None = None) -> str`: hash
  `containerfile(folder, python)`. Same prefix and same 12 hex characters.
- `require_sdk()`: in the existing `try` around `folder = folder_for(Path.cwd())`,
  also set `python = image_python(Path.cwd())`. Use the same `except (ValueError,
  subprocess.SubprocessError, OSError)` and the same message. Then
  `tag = image_tag(folder, python)`. Keep the missing-image message, and when
  `python` is set, add the Python it should hold:
  `f"--sandbox openshell: the sandbox image {tag} isn't built{f' (with Python {python}, the Python uv picks in this repo)' if python else ''}. Run {_SETUP_HINT}."`
- `OpenShell._create()`: in the existing `try` around `folder_for(self.tree)`,
  also set `python = image_python(self.tree)`, under the same `except` that
  raises `OpenShellError`. After the folder checks and before writing the
  policy, add:
  ```python
  if self.python and self.python != python:
      raise OpenShellError(
          f"the run's Python is {self.python}, which uv picks in its worktree, but the "
          f"sandbox image holds {python or 'none'}, which uv picks in the repo's main "
          f"checkout. Make them agree, as with a tracked .python-version, and run {_SETUP_HINT}.")
  ```
  `Remote.__init__` sets `self.python = ""` and `open()` sets it before
  `_create`, so the attribute always exists. This closes the gap between the
  worktree lookup that `UV_PYTHON` uses and the main-checkout lookup the image
  uses. Then pass `"--from", image_tag(folder, python)`.
- Module docstring: after "it bakes in one folder, shared by the repo and its
  worktrees, owned by the sandbox user", add that it also holds the repo's
  Python (`image_python`), installed with uv at build time because the sandbox
  can't download one. The tag covers both, so a changed Python needs `just
  openshell-setup`.

### 2. `.claude/skills/sssf/scripts/openshell_image.py`

- In `main`, in the `try` around `folder = openshell.folder_for(...)`, also set
  `python = openshell.image_python(Path(args.repo).resolve())`. Widen the
  `except` to `(ValueError, subprocess.SubprocessError, OSError)`, keeping the
  same `openshell_image: {error}` print and `return 2`.
- Use `openshell.image_tag(folder, python)` and
  `openshell.containerfile(folder, python)`.
- Docstring: "so the tag's hash covers that folder" becomes "and the Python uv
  picks in the repo, so the tag's hash covers both".

### 3. `.claude/skills/sssf/tests/test_sandbox_openshell.py` (add tests, change none)

Reuse the existing `cli` fixture and the `layout` helper. Use
`F = Path("/home/u/repo")` for pure-text tests.

- `test_containerfile_installs_and_checks_the_python`: `text = osh.containerfile(F, "3.13")`.
  - Some `RUN` line contains `uv python install 3.13`, with
    `UV_PYTHON_INSTALL_DIR=/usr/` and `UV_PYTHON_BIN_DIR=/usr/local/bin`.
  - `osh.PYTHON_DIR` and `osh.PYTHON_BIN` both start with `/usr/`, and `/usr`
    is in `osh.READ_ONLY_PATHS`.
  - The last line starting with `RUN ` ends with `&& uv python find 3.13`.
- `test_containerfile_without_a_python_installs_none`: `text = osh.containerfile(F, "")`.
  - `"uv python" not in text`.
  - `osh.image_tag(F, "") == "localhost/sssf-openshell:c1f8d90c5594"`, the
    baseline tag at `7813dbf`. Add a comment saying so.
- `test_two_pythons_give_two_image_tags`: `image_tag(F, "3.12")`,
  `image_tag(F, "3.13")` and `image_tag(F, "")` are three distinct values.
- `test_containerfile_refuses_a_malformed_python`: parametrize over `"3.13; curl
  x|sh"`, `"3"`, `"3.13.1"`, `"3.13\n"`. Each raises `ValueError` from both
  `containerfile` and `image_tag`.
- `test_require_sdk_refuses_when_the_image_for_the_hosts_python_is_missing(cli, tmp_path, monkeypatch)`:
  - Write a fake `podman` into `tmp_path / "bin"` (the fixture's dir, already
    on `PATH`) with shebang `#!{sys.executable}`. It exits 0 only for argv
    `image exists <tag>` where `<tag> == os.environ["FAKE_PODMAN_HAVE"]`, and 1
    otherwise. Mark it executable.
  - `repo, _, _ = layout(tmp_path, monkeypatch)`, `monkeypatch.chdir(repo)`,
    `folder = osh.folder_for(repo)`.
  - Set `FAKE_PODMAN_HAVE` to `osh.image_tag(folder, "3.12")`, and
    monkeypatch `osh.image_python` to `lambda p: "3.13"`.
  - `require_sdk()` raises `SystemExit`. Its text contains `openshell-setup`,
    `osh.image_tag(folder, "3.13")` and `isn't built`.
  - Then monkeypatch `osh.image_python` to `lambda p: "3.12"`. `require_sdk()`
    returns `None` (positive control).
- `test_create_builds_from_the_image_for_the_runs_python(cli, tmp_path, monkeypatch)`:
  - Use the same setup as `test_create_writes_the_policy_and_passes_no_upload`,
    with `box.python = "3.13"` and `osh.image_python` patched to `"3.13"`.
  - The `sandbox create` argv's `--from` equals `osh.image_tag(folder, "3.13")`.
- `test_create_refuses_a_python_the_image_does_not_hold(cli, tmp_path, monkeypatch)`:
  - Set `box.python = "3.13"` and patch `osh.image_python` to `"3.12"`.
  - `_create()` raises `OpenShellError` matching `openshell-setup`.
  - No `sandbox create` call appears in `cli()`.
  - No `openshell-policy.yaml` is written.

The cli fixture's `PATH` has no `uv`, so un-patched lookups in existing tests
return `""` on both sides and stay equal.

### 4. Docs

- `.claude/skills/sssf/references/config.md`, section "A local sandbox:
  `--sandbox openshell`": after the paragraph about `just openshell-setup`
  building the image with Podman, add one or two sentences:
  - The image holds the repo's Python: the major.minor uv picks in the repo's
    main checkout, the one every sandbox command gets as `UV_PYTHON`. uv
    installs it under `/usr/local` when the image is built, since the sandbox
    can't download one.
  - When that version changes, the run stops before it starts and names `just
    openshell-setup`, which builds the image for it.
- `docs/designs/openshell-sandbox.md`, "As built": after the "The image tag"
  bullet, add one bullet: **The repo's Python.** The image installs the
  major.minor uv picks in the main checkout (`image_python`) with uv at build
  time into `/usr/local`. The tag covers it, so `require_sdk` refuses a run
  whose Python changed until `just openshell-setup` rebuilds.
- `docs/open.md`, the OpenShell row (line 22): replace the closing "Next: the
  proof run, step 4" with: "Proof run 62d231db ran every agent to the end and
  its network check passed. Its one failure was the checks: `uv run` wanted the
  host's Python 3.13, which the image lacked and couldn't download. The image
  now holds the repo's Python. Next: rerun the proof run, step 4." Leave the
  other columns alone.
- No CHANGELOG exists. Leave `app_docs/` alone (see Notes for the documenter).

## Verification

From the repo root:

1. Run the skill's OpenShell tests with the runner and flags their docstring
   gives. All existing tests pass unchanged, and every new test passes:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_openshell_image.py -q`
2. Run the whole skill suite the same way (`... pytest tests -q`). It passes.
3. Run `git diff 7813dbf -- .claude/skills/sssf/tests/`. It shows only
   additions to `test_sandbox_openshell.py`: no changed or removed lines in any
   existing test.
4. Run `uv run .claude/skills/sssf/scripts/openshell_image.py --print`. It
   prints a tag and a Containerfile with `uv python install <this repo's
   major.minor>` and a last `RUN` line ending in `uv python find <same>`. It
   runs no Podman.
5. `policy()`, `READ_ONLY_PATHS` and `REGISTRIES` are unchanged
   (`git diff 7813dbf` shows no lines there).

## Out of scope

These holes stay open. A reviewer files them as old holes:

- Any network policy change. `releases.astral.sh` and GitHub stay closed.
- More than one Python per image.
- Building the image or running a sandbox. The engineer reruns the proof.
- `_host_python` running the interpreter uv picks, including a project `.venv`
  committed to the branch. The baseline already does this in the worktree at
  `open()` and in the main checkout via every `uv run` recipe.
- The bin entry being the shared `/usr/local/bin`. An apt `python3.<minor>` in
  `/usr/bin` stays, and the uv one shadows it on `PATH` when they share a minor
  version.
- The patch version isn't pinned: a rebuild installs uv's latest patch of that
  minor.
- Vercel and E2B are unchanged. They download the Python.
- The other open OpenShell findings in `docs/open.md` (shared project names, the
  writable `sssf_refuse.py`).

## Notes for the documenter

The OpenShell provider write-up in `app_docs/` (from run 32f87444) describes the
image as Node, uv, Claude Code and git on Ubuntu's Python 3.12. After this ships
it also holds the repo's Python. Note that, but don't plan or make the edit here.

## For the engineer

- The repo's own installed copy, `adws/adw_modules/sandbox_openshell.py`, is
  protected and is already modified/untracked in the working tree. After this
  lands, update it from the template (`just sssf-update`, or however you sync
  this repo's `adws/` from the skill), so this repo's runs use the new code.
- Then run `just openshell-setup` to build the image with the repo's Python, and
  rerun the proof under `--sandbox openshell` in sssf-sandbox.
