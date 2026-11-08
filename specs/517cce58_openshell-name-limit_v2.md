# Plan: keep the OpenShell sandbox name within the CLI's 19-character limit

## Problem

The first proof run in a project failed before any agent started:
`openshell sandbox create failed: name exceeds maximum length (20 > 19)`.
`OpenShell._create` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`
(line 359) names the sandbox `sssf-<adw_id>-<uuid hex[:6]>`: 5 + 8 + 1 + 6 = 20
characters. OpenShell 0.1.7 refuses a name over 19, though its `--help` doesn't
say so. A 19-character name was checked by hand and works. The labels are fine
and do not change.

The `openshell-setup` recipe in `.claude/skills/sssf/templates/justfile` also
points to `docs/research/openshell.md` for install notes, a file only the hub has.

## Threat model

The agents run as the engineer's own user. Under `--sandbox openshell` they run
inside the microVM, but the host-side code here (`_create`, `sweep`) runs on the
host as the engineer. The agent can't pick the sandbox name: it comes from the
run's `adw_id` (made by `utils.new_id()`, 8 hex characters, or read back from
the session on a resume) and a random suffix. What the agent can influence at
this level is nothing new: it can't list or name sandboxes on the host.

`sweep` deletes sandboxes, so it is the part that matters. It lists only
sandboxes carrying this project's `sssf=<project_label>` label, and it deletes
one only when the name parses as `sssf-<adw_id>-<hex>` and that `adw_id` isn't
live. Anyone (the engineer, or another tool running as them) who can make an
OpenShell sandbox with this project's label and a matching name can get it
deleted by a sweep; that was true at the baseline and this change doesn't stop
it. The guard can't stop the engineer's own user from deleting sandboxes with
the CLI directly either.

## Baseline

`main` at `59a0679` ("Add sandbox_openshell.py to the chain's guard list, to
match CLAUDE.md"). Every finding is judged against it.

At the baseline: `_create` makes 20-character names, which the CLI refuses, so
no OpenShell run can start. `sweep` parses `sssf-.+-[0-9a-f]{6}`.

## Bar

The change must:

- Make every sandbox name `sssf-<adw_id>-<hex4>`, 18 characters for an 8-character
  `adw_id`, so the CLI accepts it.
- Refuse, with `SandboxFailed` and a plain message, any name longer than the CLI's
  19-character limit, before any `openshell` command runs.
- Have `sweep` parse exactly the names `_create` makes, read the `adw_id` back from
  them, and leave alone any name that doesn't parse (the old 6-hex format included).

It must never, against the baseline:

- keep an agent file, or run agent code, anywhere it didn't;
- delete or overwrite an engineer file;
- make `sweep` delete a sandbox the baseline's sweep would have left alone, other
  than a stale sandbox in the new name format of this project (an old 6-hex name
  is now left alone, which deletes less, not more);
- add module-level state to a guard module (the guard chain counts a new
  module-level assignment as new state and stops). An `import` line is not an
  assignment and is fine.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

- Add `from .sandbox import SandboxFailed` beside the existing
  `from .sandbox_remote import Remote` import. (`sandbox_remote` already imports
  it the same way, so there's no import cycle.)
- In `class OpenShell`, directly under `session_minutes = 24 * 60`, add:

  ```python
  # The CLI's limit on a sandbox name; its --help gives none. OpenShell 0.1.7 said:
  # "name exceeds maximum length (20 > 19)"
  sandbox_name_max = 19
  ```

  It is a class attribute, not a module constant. Add no module-level name for
  it or for the suffix length.
- In `_create`, move the name to the top of the method, before `folder_for` and
  before the policy file is written, and cut the suffix to 4 hex characters:

  ```python
  name = f"sssf-{self.run_.adw_id}-{uuid.uuid4().hex[:4]}"
  if len(name) > self.sandbox_name_max:
      raise SandboxFailed(
          f"the OpenShell sandbox name {name} is {len(name)} characters, and the "
          f"openshell CLI takes at most {self.sandbox_name_max}. The run's id "
          f"{self.run_.adw_id} is too long for it.")
  ```

  Remove the old `name = ...hex[:6]` line further down. Keep the local
  `from . import repo_settings` import where it is. `SandboxFailed` isn't in
  `OpenShell.errors`, so `Remote.open` lets it through unwrapped, which is what
  the request asks for; don't add it to `errors`.
- In `sweep`, change the regex to `r"sssf-.+-[0-9a-f]{4}"` (still `re.fullmatch`).
  The `adw_id` is still `name[len("sssf-"):].rsplit("-", 1)[0]`. Nothing else in
  `sweep` changes.
- Module docstring (line 15): `sssf-<adw_id>-<hex6>` becomes `sssf-<adw_id>-<hex4>`,
  and add that the name is kept within the CLI's 19-character limit
  (`OpenShell.sandbox_name_max`).

Touch nothing else in the provider.

### 2. `.claude/skills/sssf/templates/justfile`

In the `openshell-setup` recipe (line 162), replace
`See the install notes in docs/research/openshell.md.` with a pointer to the
skill's config reference:

```
echo "openshell, openshell-gateway and podman must all be on PATH. See 'Installing OpenShell' under 'A local sandbox: --sandbox openshell' in .claude/skills/sssf/references/config.md." >&2
```

Keep the double-quoted `echo` and use only single quotes inside it. No other
recipe line changes.

### 3. `.claude/skills/sssf/references/config.md`

In `## A local sandbox: \`--sandbox openshell\``, which has no install note today,
add this paragraph right after the section's first paragraph:

```
**Installing OpenShell.** `just openshell-setup` needs `openshell`, `openshell-gateway` and `podman` on `PATH`, and installs none of them. OpenShell's release ships plain tarballs for the CLI, the gateway, the supervisor and the VM driver. Check each against the SHA-256 NVIDIA publishes for the release, then unpack them all into one folder on `PATH`, such as `~/.local/opt/openshell/bin`. That needs no root; the release's `install.sh` runs `sudo apt-get install` instead.
```

Add nothing else to config.md, and leave the rest of the section as it is.

### 4. `docs/designs/openshell-sandbox.md`

In `## As built`, replace the **Sweep** bullet (line 77) with:

```
- **The sandbox name** is `sssf-<adw_id>-<hex4>`: 18 characters for an 8-character adw_id. OpenShell 0.1.7 refuses a name over 19 characters ("name exceeds maximum length (20 > 19)"), though its `--help` doesn't say so, so `OpenShell.sandbox_name_max` is 19 and `_create` raises `SandboxFailed` for a longer name before calling the CLI. **Sweep** reads the adw_id from the name, so `sandbox list --names` is enough; a name that doesn't parse, the old `<hex6>` form included, is left alone.
```

### 5. `.claude/skills/sssf/tests/test_sandbox_openshell.py`

Import `utils` alongside the existing imports:
`from adw_modules import agents, repo_settings, sandbox, sandbox_openshell as osh, utils`.

Add a helper beside `layout()` that puts the config SSSF ships into the test repo,
the same way `test_data_dir_reads_the_shipped_config` does:

```python
def write_shipped_config(repo: Path) -> None:
    config = repo / osh.CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text((SKILL / "templates" / "sssf.config.yaml").read_text())
```

(The file already has a `shipped()` that returns the agent roster; use the
helper's different name, and leave `shipped()` and `layout()` as they are.)

- **Update** `test_sweep_deletes_only_dead_runs_of_this_project` (it pins the old
  suffix length): names become `sssf-live1-aaaa`, `sssf-dead1-bbbb`, plus an
  old-format `sssf-dead2-cccccc` and `other-thing`. Assert
  `osh.sweep("proj", {"live1"}) == ["sssf-dead1-bbbb (dead1)"]` and that the only
  delete call is `["sandbox", "delete", "sssf-dead1-bbbb"]`, so the 6-hex name is
  left alone. Keep the selector assertion.
- **Add** `test_every_name_create_makes_fits_the_cli_and_sweeps_back`: use
  `repo, run, _ = layout(tmp_path, monkeypatch)`, then `write_shipped_config(repo)`,
  then `monkeypatch.chdir(repo)`. Set `run.adw_id = utils.new_id()` (the real
  generator) and call `osh.OpenShell(run, "sssf/x", "my repo")._create()` three
  times. For each `vm.name`: `len(vm.name) <= osh.OpenShell.sandbox_name_max`,
  it equals the `--name` passed to the matching `sandbox create` call, and it
  starts with `f"sssf-{run.adw_id}-"`. Then set `FAKE_OPENSHELL_NAMES` to the names
  joined by newlines and assert
  `osh.sweep("my repo", set()) == [f"{n} ({run.adw_id})" for n in names]`,
  so every made name parses and gives back the run's `adw_id`.
- **Add** `test_create_refuses_a_name_over_the_cli_limit`: `layout(...)`, then
  `write_shipped_config(repo)`, then `monkeypatch.chdir(repo)`, and
  `run.adw_id = "a" * 10` (the name would be 20). Assert
  `pytest.raises(sandbox.SandboxFailed, match="19")` around `_create()`, and that
  `cli()` holds no `["sandbox", "create"]` call. Also assert
  `osh.OpenShell.sandbox_name_max == 19`.
- **Add** `test_setup_recipe_points_to_the_skills_config_reference`: read
  `SKILL / "templates" / "justfile"`, take the text of the `openshell-setup:`
  recipe (from that line to the next line that starts a new recipe or comment
  block at column 0), and assert it contains `references/config.md` and not
  `docs/research`. Assert `SKILL / "references" / "config.md"` contains
  `**Installing OpenShell.**` and `SHA-256`.

All other existing tests stay as they are. `test_create_writes_the_policy_and_passes_no_upload`
asserts `startswith("sssf-abc-")`, which still holds.

### Docs this change makes out of date

- The module docstring of `sandbox_openshell.py` (step 1).
- `docs/designs/openshell-sandbox.md` "As built" (step 4).
- `references/config.md` install note (step 3) and the justfile help text (step 2).
- No CHANGELOG exists in the repo or the skill. No `app_docs/` write-up describes
  the sandbox name, so the documenter has nothing there to note.
- `docs/research/openshell.md` stays as it is: it records the 2026-11-07 test.
- Older specs under `specs/` that name `<hex6>` are records; leave them.

## Verification

Run from the repo root (the same command `adws/adw_modules/quality.py` runs):

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
```

All tests pass, including the three new ones and the updated sweep test. Then:

- `grep -n 'hex\[:4\]' .claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`
  shows the one name line, and `grep -n '{6}'` on the same file shows nothing.
- `grep -n 'docs/research' .claude/skills/sssf/templates/justfile` shows nothing.
- The diff of `sandbox_openshell.py` adds no module-level assignment: only the
  import line and the class attribute.

## Out of scope

Old holes this change leaves open, to be filed as such:

- `sweep` trusts any sandbox carrying this project's label and a parsing name,
  whoever made it (see Threat model).
- A sandbox left over from a baseline run, in the 6-hex name format, is no longer
  swept. None can exist, since the CLI refused the baseline's names.
- An `adw_id` over 9 characters now fails under `--sandbox openshell` with
  `SandboxFailed` before any CLI call. `utils.new_id()` makes 8, so only a
  hand-picked or foreign id hits this; the baseline failed it at the CLI instead.
- The `adw_id` label still goes through `label()`, not `project_label()`; the
  request leaves labels as they are.
- Everything else in the provider: the image, the policy, exec framing, denial
  logs, the readiness loop, `require_sdk`.
