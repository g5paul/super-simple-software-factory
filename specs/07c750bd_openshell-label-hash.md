# Make the OpenShell project label exact

## Summary

`sandbox_openshell.label` makes the `sssf=` label by replacing every character
outside `[A-Za-z0-9_.-]` with `-` and cutting the result at 63 characters. That
loses information, so `my app` / `my-app`, `app+x` / `app-x`, `café` / `caf-`,
and two 64-character names that differ only in the last character all get the
same label. `sweep` lists by that label, so one project's sweep deletes another
project's live sandboxes, and any work they hadn't committed is lost.

The fix: a new function `project_label(project)` returns a readable cleaned
prefix, then `-`, then 12 hex characters of the SHA-256 of the exact name.
`_create` and `sweep` both call it. The `adw_id` label keeps the old `label()`.

## Baseline

Commit `5dc3891` ("Add --sandbox openshell, a local microVM provider driven by
the openshell CLI"), at `5dc3891`. Every finding is judged against it. At the
baseline, `sandbox_openshell.py` has `label(value)` (lossy), and `_create` and
`sweep` both call `label(self.project)` / `label(project)`.

## Threat model

- The agent runs as the engineer's own user. On the host (a run without
  `--sandbox`), it can run the `openshell` CLI itself: list, create, label or
  delete any sandbox, from any project. This guard can't stop that, and this
  change doesn't try to.
- Under `--sandbox openshell`, the agent is inside the VM and its network policy
  doesn't open the gateway. The host process (SSSF) is the one that runs
  `sandbox create` and `sandbox list` / `delete`.
- What this change defends against is SSSF's own sweep, run honestly for project
  A, matching sandboxes that project B's run created, because the two names map
  to the same label. That is an accident between two projects, not an attack.
- Someone who controls a project's folder name could look for a name whose
  12-hex (48-bit) digest matches another project's. That person is already the
  engineer's user, who can delete the sandboxes directly, so the label doesn't
  need to withstand that.

## Bar

Against the baseline, the change **must**:

1. Give two distinct project names (distinct Python `str`s) two distinct `sssf=`
   values, unless their SHA-256 digests collide in the first 48 bits.
2. Make every `sssf=` value a valid label value: 1 to 63 characters, only
   `[A-Za-z0-9_.-]`, starting and ending with a letter or digit. This holds for
   any name, including an empty one, a 200-character one, one that is entirely
   non-ASCII, and one with lone surrogates (a folder name decoded with
   `surrogateescape`).
3. Have `_create` and `sweep` call the same function, so for a given project
   the label `_create` sets equals the value in the selector `sweep` lists with.

The change must **never**:

- Keep an agent file, run agent code, or delete or overwrite an engineer file
  where the baseline doesn't. The change only computes a string. It adds no
  file write, no subprocess, and no deletion.
- Change the `adw_id=` label, the sandbox name format `sssf-<adw_id>-<hex6>`,
  the way `sweep` parses names, its live-run check, or any other argv `_create`
  or `sweep` passes.
- Make `sweep` delete anything the baseline wouldn't for the same project name,
  when that name has no collision under the baseline. The new selector matches
  only sandboxes that the new `_create` labelled.

## Why the help text doesn't settle the limit

`~/.local/opt/openshell/bin/openshell sandbox create --help` (exit 0) says only
`--label <LABELS>  Attach labels to the sandbox (key=value format, repeatable)`.
`sandbox list --help` says `--selector <SELECTOR>  Filter sandboxes by label
selector (key1=value1,key2=value2)`. Neither gives a length or a character set.
The gateway isn't running on this machine (`sandbox list` gives `connect error:
transport error`), so the limits can't be tested live. OpenShell has a
Kubernetes driver (`--driver-config-json` mentions it), and the baseline's
`[:63]` was already assuming Kubernetes label-value rules. So the plan uses
the strictest common rule, the Kubernetes label value: at most 63 characters,
matching `([A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?)`, which also means no `,`
or `=`, so the selector parses. That is stricter than the baseline, which could
end a label with `-` (for example `caf-`) or start one with `.`.

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

1. Add constants next to `_TIMEOUT`:
   ```python
   LABEL_MAX = 63       # a Kubernetes label value's limit; the CLI's help gives none
   LABEL_HASH = 12      # hex characters of the name's SHA-256 in the project label
   ```
2. Leave `label(value)` exactly as it is. `adw_id` still uses it.
3. Add, right after `label`:
   ```python
   def project_label(project: str) -> str:
       """The `sssf=` label for a project: its name cleaned to [A-Za-z0-9_.-] and cut,
       then `-` and 12 hex of the SHA-256 of the exact name, so distinct names get
       distinct labels. At most 63 characters, starting and ending with a letter or
       digit."""
       digest = hashlib.sha256(project.encode("utf-8", "surrogatepass")).hexdigest()[:LABEL_HASH]
       prefix = re.sub(r"[^A-Za-z0-9_.-]", "-", project)[:LABEL_MAX - LABEL_HASH - 1]
       prefix = re.sub(r"^[^A-Za-z0-9]+", "", prefix)
       return f"{prefix}-{digest}" if prefix else digest
   ```
   - Use `surrogatepass`, not `surrogateescape`. `surrogateescape` maps
     `"caf\udcc3\udca9"` and `"café"` to the same bytes. `surrogatepass` gives
     each distinct `str` distinct bytes, and it never raises on a lone surrogate.
   - The value always ends in a hex digit. It starts with a letter or digit,
     because leading non-alphanumerics are stripped and an empty prefix leaves
     the digest alone. The longest value is 50 + 1 + 12 = 63 characters.
   - Inside the prefix, `.`, `_` and `-` are allowed, so runs like `caf--…` are
     valid.
4. In `sweep`: change `f"sssf={label(project)}"` to `f"sssf={project_label(project)}"`.
5. In `OpenShell._create`: change `f"sssf={label(self.project)}"` to
   `f"sssf={project_label(self.project)}"`. Leave
   `f"adw_id={label(self.run_.adw_id)}"` as it is.
6. Module docstring: after the `sweep` paragraph, add one sentence: "The `sssf`
   label is `project_label(project)`: a readable prefix and a hash of the exact
   name, so no two projects share one."

No other line in the module changes.

### `.claude/skills/sssf/tests/test_sandbox_openshell.py`

Two existing assertions pin the old label's literal value. They have to change,
because each asserts the lossy mapping this change removes. The request asks for
existing tests to pass unchanged, and these two are the only exceptions; no
other existing line changes:

- In `test_create_writes_the_policy_and_passes_no_upload`, change
  `assert "sssf=my-repo" in create and "adw_id=abc" in create`
  to `assert f"sssf={osh.project_label('my repo')}" in create and "adw_id=abc" in create`.
  (`"sssf=my-repo"` is the very label that `my-repo` would share.)
- In `test_sweep_deletes_only_dead_runs_of_this_project`, change
  `"sssf=proj"` to `f"sssf={osh.project_label('proj')}"`.

Add these tests to the same file. They use the existing `cli` fixture and
`layout` helper. They need no new fixture and no test-only config.

```python
LABEL_VALUE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9_.-]*[A-Za-z0-9])?")   # add `import re`

COLLIDING = [("my app", "my-app"), ("app+x", "app-x"), ("café", "caf-"),
             ("a" * 63 + "b", "a" * 63 + "c")]


@pytest.mark.parametrize("one,two", COLLIDING)
def test_names_the_old_label_merged_get_distinct_labels(one, two):
    assert osh.label(one) == osh.label(two)            # the baseline's collision
    assert osh.project_label(one) != osh.project_label(two)


@pytest.mark.parametrize("name", [n for pair in COLLIDING for n in pair] + [
    "x" * 200, "日本語のプロジェクト", "café" * 60, "", "-lead", "trail-", ".dot", "_u",
    "a,b=c", "my repo", "caf\udcc3\udca9", "\udcff" * 3])
def test_every_project_label_passes_openshell_limits(name):
    value = osh.project_label(name)
    assert len(value) <= osh.LABEL_MAX and LABEL_VALUE.fullmatch(value)
    assert value == osh.project_label(name)


def test_the_label_keeps_a_readable_prefix():
    assert osh.project_label("my-repo").startswith("my-repo-")
    assert osh.project_label("café") != osh.project_label("caf\udcc3\udca9")


def test_create_labels_what_this_projects_sweep_lists(cli, tmp_path, monkeypatch):
    repo, run, _ = layout(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    osh.OpenShell(run, "sssf/abc", "my repo")._create()
    create = next(c["argv"] for c in cli() if c["argv"][:2] == ["sandbox", "create"])
    made = [create[i + 1] for i, a in enumerate(create) if a == "--label"
            and create[i + 1].startswith("sssf=")]
    osh.sweep("my repo", set())
    osh.sweep("my-repo", set())
    selectors = [c["argv"][3] for c in cli() if c["argv"][:3] == ["sandbox", "list", "--selector"]]
    assert made == [selectors[0]]
    assert selectors[1] != selectors[0]
```

`FAKE_OPENSHELL_NAMES` is unset in that last test, so the fake `list` prints an
empty line and `sweep` deletes nothing.

### `docs/designs/openshell-sandbox.md`

In `## As built`, add one bullet after the `**Sweep**` bullet:

- **The project label.** `sssf=` is the project name cleaned to `[A-Za-z0-9_.-]` and cut to 50 characters, then `-` and the first 12 hex characters of the exact name's SHA-256, so two projects never share one, and it stays within a Kubernetes label value's 63 characters.

Leave step 13 ("`sweep` lists sandboxes labelled `sssf=<project>`") as it is.
It names the label key, and the bullet above says how the value is made.

## Docs this change makes out of date

- `docs/designs/openshell-sandbox.md` "As built": covered above.
- The `sandbox_openshell.py` module docstring: covered above.
- `.claude/skills/sssf/references/config.md` § "A local sandbox: `--sandbox openshell`"
  doesn't mention the label, so it needs no change.
- No CHANGELOG exists in this repo.
- `app_docs/` has no OpenShell write-up yet, so the documenter has nothing old to
  flag. Don't plan an edit there.

## Out of scope (old holes)

- **Same-named repos (the review's hole 2).** Two repos whose main checkouts have
  the same folder name are one project in every provider, because the project
  is that folder's name. This change keeps that: identical names still get
  identical labels.
- **Sandboxes made by the baseline.** They carry the old lossy label, so the new
  `sweep` doesn't list them and they are left running, never deleted. The
  baseline only exists on this branch. An engineer who ran it can delete those
  sandboxes with `openshell sandbox delete <name>`. Leaking them is the safe
  direction for the bar.
- **An agent running the `openshell` CLI on the host.** Without `--sandbox`, an
  agent can delete or relabel any sandbox. This is the threat model's limit.
- **A deliberate 48-bit digest collision.** Finding one takes control of a
  project's folder name and about 2^48 tries. That person is already the
  engineer's user.
- **The live label limits.** The plan uses Kubernetes label-value rules, because
  the CLI documents none and no gateway was up to test against. The provider's
  proof run is where a live `sandbox create` would show a different limit.
- **Everything else in the provider:** the policy, exec, the sentinel, and
  `_call_end`.

## Verification

The repo's own quality suite runs the hub tests (`adws/adw_modules/quality.py`'s
`hub_test` step), so no extra check is needed. To run just this file, from the
repo root:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py -q -p no:cacheprovider
```

Expected results:

- The four new `test_names_the_old_label_merged_get_distinct_labels` cases pass.
  Each also asserts that the baseline `label()` collides, so the cases prove the
  hole was real.
- Every case of `test_every_project_label_passes_openshell_limits` passes,
  including the 200-character, non-ASCII, empty and lone-surrogate names.
- `test_create_labels_what_this_projects_sweep_lists` passes.
- The existing 20 tests pass, with only the two literal-label assertions changed
  as described.
- `git diff 5dc3891 -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`
  shows only the two constants, `project_label`, the two call sites, and the
  docstring sentence. `label()` and the `adw_id=` argument are unchanged.
