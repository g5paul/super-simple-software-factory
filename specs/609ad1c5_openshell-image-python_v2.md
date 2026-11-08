# Ship the repo's Python in the OpenShell image

## Summary

Proof run 62d231db ran every agent to the end under `--sandbox openshell`, then
failed its checks. Each VM command gets `UV_PYTHON` from
`sandbox_remote._host_python`, which was 3.13 there, but the image has only
Ubuntu's Python 3.12. `uv run pytest` couldn't download 3.13 because the policy
blocks `releases.astral.sh` and GitHub.

The fix: the image installs the repo's Python with uv at build time, when the
build has network. The version is in the Containerfile, so it is in the image
tag. If the Python changed since the image was built, `require_sdk` stops the
run with the message that names `just openshell-setup`, as it does for a missing
image today.

Every code change goes in the skill's template copy,
`.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`, and in
`.claude/skills/sssf/scripts/openshell_image.py`. The new tests go in
`.claude/skills/sssf/tests/test_sandbox_openshell.py`.

Revision 2 makes two changes after spec review 1:
- It drops the `_create` Python-mismatch refusal and its test. The divergence
  it caught is now listed under Out of scope.
- Every new test built on `layout` now calls `write_shipped_config(repo)`.

## Threat model

- **Who runs what.** Agents run in the OpenShell microVM as `ubuntu`. The host
  side runs as the engineer's own user: `require_sdk`, `_create`,
  `scripts/openshell_image.py` and `just openshell-setup`. The image build runs
  under Podman as that user, with network.
- **Where the version comes from.** The host's `uv python find` runs in the
  repo's main checkout. `_host_python` then runs the interpreter uv found with
  `-c "...'%d.%d'..."`. That lookup reads repo files: `.python-version`,
  `requires-python` in `pyproject.toml`, and a project `.venv`. An agent's file
  gets into the main checkout only when the engineer merges it.
- **What this guard can't stop at its level.** The engineer's user can run
  anything, and once a change is merged the host trusts it. That includes a
  committed `.venv/bin/python` that uv would pick. The baseline already runs
  that interpreter in the main checkout, because every `just` recipe is
  `uv run adws/...` from the root. It also runs `_host_python` in the worktree
  at `open()`.
- **The new risk.** The version string now ends up in the Containerfile, whose
  `RUN` lines Podman runs as shell. `containerfile` accepts only `""` or a
  version matching `^\d+\.\d+$`, and raises `ValueError` for anything else, so
  no text from the host lookup can add a command to the build.

## Baseline

`main` at `7813dbf` ("Start VM agents with setsid -w, so OpenShell keeps their
stream"). Every finding is judged against it. At the baseline:

- `containerfile(folder)` installs no Python beyond apt's `python3`, which is
  3.12.
- `image_tag(folder)` hashes that text alone. For `Path("/home/u/repo")` the tag
  is `localhost/sssf-openshell:c1f8d90c5594`, worked out by running the baseline
  code.
- `require_sdk` and `_create` never look up a Python.
- `Remote.open` sets `self.python = _host_python(self.tree)` from the worktree
  and passes it as `UV_PYTHON`.

## Bar

The change must:
- Install the version the host's uv picks in the repo's main checkout into the
  image. It goes under `/usr`, as a `python3.<minor>` on `PATH` that the sandbox
  user's uv finds with no environment variable of its own.
- End the build with a `RUN` line that fails unless
  `uv python find <version>` succeeds.
- Get the version from one function, `image_python`, in all three callers:
  `require_sdk`, `_create` and `scripts/openshell_image.py`. All three then
  agree on the tag.
- Make `require_sdk` refuse, naming `just openshell-setup`, when the image for
  the host's current Python isn't built.
- With no host Python (`""`), give exactly the baseline Containerfile text and
  tag.

It must never:
- Put a version that isn't `\d+\.\d+` into the Containerfile.
- Open any network rule, or change `policy()`, `READ_ONLY_PATHS` or
  `REGISTRIES`.
- Refuse a run that the baseline lets start, other than by the missing-image
  check in `require_sdk`.
- Keep an agent file, run agent code the baseline doesn't run, or delete or
  overwrite an engineer file.
- Build an image or create a sandbox from code or tests.
- Change any existing test.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

Read the whole file first.

- Add `from . import sandbox_remote` next to `from .sandbox_remote import Remote`.
  Call `sandbox_remote._host_python` through the module so tests can patch it.
- Add these constants near `NODE`:
  ```python
  PYTHON_DIR = "/usr/local/share/uv/python"   # under /usr, which the policy reads
  PYTHON_BIN = "/usr/local/bin"               # on PATH, so uv finds python3.<minor>
  ```
- Add this function in the layout section, after `folder_for`:
  ```python
  def image_python(path: Path) -> str:
      """The Python, as major.minor, the image holds for the repo `path` is in:
      what the host's uv picks in its main checkout, the value a run passes as
      UV_PYTHON (sandbox_remote._host_python). "" when the host has none."""
      return sandbox_remote._host_python(repo_root(path))
  ```
  It catches nothing. `repo_root` raises the same errors `folder_for` already
  raises, and each caller catches them together.
- Change `containerfile` to `containerfile(folder: Path, python: str | None = None) -> str`:
  - **The default.** `None` means `image_python(folder)`. In the default layout
    the folder is the main checkout, so this gives the same version the callers
    pass. The default keeps the existing one-argument calls working. Spec review
    1 confirmed it's needed: `test_the_script_and_provider_agree_on_the_tag` runs
    with the real `PATH`, where uv is, and calls `image_tag(folder)`.
  - **Validation.** If `python` is non-empty and
    `re.fullmatch(r"\d+\.\d+", python)` fails, raise
    `ValueError(f"not a Python major.minor: {python!r}")`.
  - **The install line.** When `python` is non-empty, insert this line straight
    after the uv install `RUN` line, before `npm install`:
    `f"RUN UV_PYTHON_INSTALL_DIR={PYTHON_DIR} UV_PYTHON_BIN_DIR={PYTHON_BIN} uv python install {python}"`
    - By default `uv python install` puts a `python3.<minor>` executable in the
      bin dir. Its `--help` says: "only a `python{major}.{minor}` executable is
      installed".
    - `--install-dir` has the env form `UV_PYTHON_INSTALL_DIR`.
    - `UV_PYTHON_BIN_DIR` sets the bin dir that `uv python dir --bin` reports.
  - **The last line.** The last `RUN` line stays
    `RUN node --version && uv --version && claude --version && git --version`.
    When `python` is non-empty, append `f" && uv python find {python}"`.
  - **No Python.** With `python == ""` the text must match the baseline byte for
    byte. Only add lines, and only when there's a version. Don't reorder or
    reword the existing ones.
- Change `image_tag` to `image_tag(folder: Path, python: str | None = None) -> str`.
  It hashes `containerfile(folder, python)`, with the same prefix and the same 12
  hex characters.
- `require_sdk()`:
  - In the `try` around `folder = folder_for(Path.cwd())`, also set
    `python = image_python(Path.cwd())`. Keep the same
    `except (ValueError, subprocess.SubprocessError, OSError)` and the same
    message.
  - Then set `tag = image_tag(folder, python)`.
  - Keep the missing-image message, adding the Python when there is one:
    `f"--sandbox openshell: the sandbox image {tag} isn't built{f' (with Python {python}, the Python uv picks in this repo)' if python else ''}. Run {_SETUP_HINT}."`
- `OpenShell._create()`:
  - In the `try` around `folder_for(self.tree)`, also set
    `python = image_python(self.tree)`. It goes under the same `except`, which
    raises `OpenShellError`.
  - Pass `"--from", image_tag(folder, python)`.
  - Don't compare `python` with `self.python`, and add no new refusal.
- Module docstring: after "it bakes in one folder, shared by the repo and its
  worktrees, owned by the sandbox user", add three points:
  - The image also holds the repo's Python (`image_python`).
  - uv installs it at build time, because the sandbox can't download one.
  - The tag covers both, so a changed Python needs `just openshell-setup`.

### 2. `.claude/skills/sssf/scripts/openshell_image.py`

- In `main`, inside the `try` around `folder = openshell.folder_for(...)`:
  - Also set `python = openshell.image_python(Path(args.repo).resolve())`.
  - Widen the `except` to `(ValueError, subprocess.SubprocessError, OSError)`.
    Keep the same `openshell_image: {error}` print and `return 2`.
- Use `openshell.image_tag(folder, python)` and
  `openshell.containerfile(folder, python)`.
- In the docstring, "so the tag's hash covers that folder" becomes "and the
  Python uv picks in the repo, so the tag's hash covers both".

### 3. `.claude/skills/sssf/tests/test_sandbox_openshell.py`: add tests, change none

Reuse the existing `cli` fixture and the `layout` and `write_shipped_config`
helpers. Every new test that calls `layout` calls `write_shipped_config(repo)`
straight after, so `folder_for` reads the `data_dir` SSSF ships. The pure-text
tests use `F = Path("/home/u/repo")`.

- **`test_containerfile_installs_and_checks_the_python`.**
  Set `text = osh.containerfile(F, "3.13")`.
  - One `RUN` line contains `uv python install 3.13`, along with
    `UV_PYTHON_INSTALL_DIR=/usr/` and `UV_PYTHON_BIN_DIR=/usr/local/bin`.
  - `osh.PYTHON_DIR` and `osh.PYTHON_BIN` both start with `/usr/`, and `/usr`
    is in `osh.READ_ONLY_PATHS`.
  - The last line starting `RUN ` ends with `&& uv python find 3.13`.
- **`test_containerfile_without_a_python_installs_none`.**
  Set `text = osh.containerfile(F, "")`.
  - `"uv python"` is not in `text`.
  - `osh.image_tag(F, "") == "localhost/sssf-openshell:c1f8d90c5594"`. Add a
    comment that this is the tag at `7813dbf`.
- **`test_two_pythons_give_two_image_tags`.** `image_tag(F, "3.12")`,
  `image_tag(F, "3.13")` and `image_tag(F, "")` are three distinct values.
- **`test_containerfile_refuses_a_malformed_python`.** Parametrize over
  `"3.13; curl x|sh"`, `"3"`, `"3.13.1"` and `"3.13\n"`. Each raises `ValueError`
  from `containerfile` and from `image_tag`.
- **`test_require_sdk_refuses_when_the_image_for_the_hosts_python_is_missing(cli, tmp_path, monkeypatch)`.**
  1. Write a fake `podman` into `tmp_path / "bin"`, the fixture's dir, which is
     already on `PATH`. Give it the shebang `#!{sys.executable}`. It exits 0
     only for the argv `image exists <tag>` with
     `<tag> == os.environ["FAKE_PODMAN_HAVE"]`, and 1 for anything else. Make
     it executable.
  2. Run `repo, _, _ = layout(tmp_path, monkeypatch)`, then
     `write_shipped_config(repo)`, then `monkeypatch.chdir(repo)`, then
     `folder = osh.folder_for(repo)`.
  3. Set `FAKE_PODMAN_HAVE` to `osh.image_tag(folder, "3.12")`, and patch
     `osh.image_python` to `lambda p: "3.13"`.
  4. `require_sdk()` raises `SystemExit`. Its text contains `openshell-setup`,
     `osh.image_tag(folder, "3.13")` and `isn't built`.
  5. Positive control: patch `osh.image_python` to `lambda p: "3.12"`. Now
     `require_sdk()` returns `None`.
- **`test_create_builds_from_the_image_for_the_hosts_python(cli, tmp_path, monkeypatch)`.**
  1. Set up as in `test_create_writes_the_policy_and_passes_no_upload`, plus
     `write_shipped_config(repo)`.
  2. Patch `osh.image_python` to `lambda p: "3.13"`.
  3. In the `sandbox create` argv, `--from` equals
     `osh.image_tag(osh.folder_for(repo), "3.13")`.

The `cli` fixture's `PATH` has no `uv`, so lookups the existing tests don't
patch return `""` on both sides and stay equal.

### 4. Docs

- **`.claude/skills/sssf/references/config.md`**, section "A local sandbox:
  `--sandbox openshell`". After the paragraph saying `just openshell-setup`
  builds the image with Podman, add one or two sentences saying:
  - The image holds the repo's Python. That is the major.minor uv picks in the
    repo's main checkout, the one every sandbox command gets as `UV_PYTHON`.
  - uv installs it under `/usr/local` when the image is built, since the sandbox
    can't download one.
  - When that version changes, the run stops before it starts and names
    `just openshell-setup`, which builds the image for it.
- **`docs/designs/openshell-sandbox.md`**, "As built". Add one bullet after "The
  image tag":
  **The repo's Python.** The image installs the major.minor uv picks in the main
  checkout (`image_python`) with uv at build time, under `/usr/local`. The tag
  covers it, so `require_sdk` refuses a run whose Python changed until
  `just openshell-setup` rebuilds.
- **`docs/open.md`**, the OpenShell row (line 22). Replace the closing "Next: the
  proof run, step 4" with:
  "Proof run 62d231db ran every agent to the end and its network check passed.
  Its one failure was the checks: `uv run` wanted the host's Python 3.13, which
  the image lacked and couldn't download. The image now holds the repo's Python.
  Next: rerun the proof run, step 4."
  Leave the other columns alone.
- There is no CHANGELOG. Leave `app_docs/` alone; see Notes for the documenter.

## Verification

Run everything from the repo root.

1. Run the skill's OpenShell tests with the runner and flags from their
   docstring:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_openshell_image.py -q`
   Every existing test passes unchanged, and every new one passes.
2. Run the whole skill suite the same way (`... pytest tests -q`). It passes.
3. Run `git diff 7813dbf -- .claude/skills/sssf/tests/`. It shows only
   additions, all in `test_sandbox_openshell.py`. No existing test has a changed
   or removed line.
4. Run `uv run .claude/skills/sssf/scripts/openshell_image.py --print`. It runs
   no Podman. It prints:
   - a tag;
   - a Containerfile with `uv python install <this repo's major.minor>`;
   - a last `RUN` line ending in `uv python find <the same version>`.
5. `git diff 7813dbf` shows no change to `policy()`, `READ_ONLY_PATHS`,
   `REGISTRIES`, or `_create`'s refusals.

## Out of scope

The holes below stay open. A reviewer files them as old holes.

- **The network policy.** `releases.astral.sh` and GitHub stay closed.
- **Worktree and main checkout picking different Pythons.** `UV_PYTHON` comes
  from the worktree (`Remote.open`), but the image's Python comes from the main
  checkout. They can differ, for example with an untracked `.python-version` in
  the main checkout only.
  - When the worktree's version isn't in the image, the run fails the way it
    does at baseline: uv's download is denied.
  - When it's apt's 3.12, it works as at baseline.
  - Spec review 1 kept this check out on purpose.
- **More than one Python per image.**
- **Building the image or running a sandbox.** The engineer reruns the proof.
- **`_host_python` runs the interpreter uv picks.** That can be a project
  `.venv` committed to the branch. The baseline already does this: in the
  worktree at `open()`, and in the main checkout through every `uv run` recipe.
- **The shared bin dir.** The bin entry goes in `/usr/local/bin`, which other
  tools share. An apt `python3.<minor>` in `/usr/bin` stays. When both have the
  same minor version, the uv one comes first on `PATH`.
- **The patch version isn't pinned.** A rebuild installs uv's latest patch of
  that minor version.
- **Vercel and E2B are unchanged.** They download their Python.
- **The other open OpenShell findings in `docs/open.md`:** shared project names
  and the writable `sssf_refuse.py`.

## Notes for the documenter

The OpenShell provider write-up in `app_docs/`, from run 32f87444, describes the
image as Node, uv, Claude Code and git on Ubuntu's Python 3.12. After this ships
the image also holds the repo's Python. Note that in your write-up. This plan
doesn't edit `app_docs/`.

## For the engineer

- This repo's own installed copy, `adws/adw_modules/sandbox_openshell.py`, is
  protected, and the working tree already has it modified or untracked. After
  this lands, update it from the template (`just sssf-update`, or however you
  sync this repo's `adws/` from the skill), so this repo's runs use the new code.
- Then run `just openshell-setup` to build the image with the repo's Python, and
  rerun the proof under `--sandbox openshell` in sssf-sandbox.
