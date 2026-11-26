# Let pytest walk a temp folder in an OpenShell VM

## Summary

In an OpenShell VM every exec gets `TMPDIR=/var/tmp`. The VM's filesystem policy lets a process read and write `/var/tmp` and read `/var/log`, but nothing covers `/var` itself, so no process can list `/var`. pytest lists every parent of a test file that lies outside its rootdir, so a test file under `/var/tmp` fails to collect (run 12ec0782's plan check).

The fix is one entry: add `/var` to `READ_ONLY_PATHS` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`. A new test checks that every parent of `TMPDIR` below `/` is covered by the policy. Three existing tests that pin the read-only list gain the one entry. Three docs change.

This is the request's option 1. Option 2 (move `TMPDIR`) is not taken; see "Why option 1" below.

## Baseline

`main` at `53947df` ("Brief: let pytest walk a temp folder in an OpenShell VM"). Every finding is judged against that commit.

At the baseline, in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`:

- `READ_ONLY_PATHS` is `["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero", "/dev/random", "/etc", "/var/log"]` (lines 71-72).
- `TMPDIR = "/var/tmp"` (line 73).
- `policy()` writes `read_only: list(READ_ONLY_PATHS)` and `read_write: ["/tmp", TMPDIR, "/dev/null", str(folder)]` (lines 204-205).
- `tests/test_sandbox_openshell.py` passes on the host under agentsh: 104 passed with `-m 'not pushes and not host_only'` (the planner ran it on 2026-11-26).

## What the planner checked

- **pytest's walk.** pytest 9.2.0, `_pytest/main.py`, `Session.collect`: for a path argument it loops over `argpath.parents` and stops at the first parent for which `pm._is_in_confcutdir(path)` is false. `_is_in_confcutdir` returns `path not in self._confcutdir.parents`. The confcutdir is always set (the ini file's folder, or the rootdir when there is no ini file). So for `/var/tmp/tmp.x/src/test_kept.py` with the repo under `/home/...`, the walk keeps `src`, `tmp.x`, `/var/tmp` and `/var`, and stops at `/`, because `/` is a parent of the confcutdir. pytest then collects `/var` as a directory, which calls `os.scandir("/var")`. That is the `PermissionError: [Errno 13] Permission denied: '/var'` in 12ec0782's log.
- **`/` is never listed.** The confcutdir is an absolute path, so `/` is always one of its parents (or the confcutdir itself is `/`, which needs a repo at `/`, and SSSF's image puts the repo under the home folder). The planner found no case where pytest lists `/`. Nothing in this plan widens `/`.
- **Landlock grants are additive** (`docs/research/openshell.md`, line 62). A read-only rule on `/var` and a read-write rule on `/var/tmp` give read-write under `/var/tmp` and read-only for the rest of `/var`. The baseline has no read-only parent with a read-write child, so the engineer's VM proof is the first run of that shape. If OpenShell refuses it, the sandbox doesn't start (`compatibility: hard_requirement`), which fails closed.

## Why option 1

- It is one entry in one list. `TMPDIR` stays `/var/tmp`, on the VM's disk, so the skill suite's 2.5 GB of temp files stays where run 00d8e1a8's fix put it.
- Option 2 needs a new folder that exists in the image (Landlock applies a rule only to a path that exists when the sandbox starts, per `docs/designs/openshell-sandbox.md`), is owned by the sandbox user, is outside the repo's tree, is not `/tmp`, and has every parent below `/` covered. The candidates are under `/usr` or `/etc`, which are read-only system trees, or under the baked shared folder, which is the repo's parent and so too near the repo's tree. Each needs a Containerfile change, which changes the image tag and makes every repo run `just openshell-setup` again. Option 1 changes no image.
- What option 1 costs is that an agent in the VM can read `/var`. The next section says why that gives it nothing.

### What `/var` holds in the image

From reading `containerfile()` and what Ubuntu 24.04's base image ships. The planner can't start a VM, so this is not read from one; the engineer's proof checks it.

| Path | What it holds |
| --- | --- |
| `/var/backups`, `/var/local`, `/var/mail`, `/var/opt` | Empty folders. |
| `/var/cache` | `apt` (emptied archives), `debconf` (`config.dat`, `templates.dat`, and `passwords.dat`, which is mode 0600 root and empty), `ldconfig` (mode 0700 root). |
| `/var/lib` | `dpkg` (the package database), `apt` (its `lists` are removed by the Containerfile's `rm -rf /var/lib/apt/lists/*`), `pam`, `systemd`, `misc`, and small state files of the installed packages (`git`, `curl`, `ca-certificates`, `python3`, `xz-utils`, `ripgrep`, `fd-find`, `rsync`). |
| `/var/log` | Already read-only at the baseline: `apt`, `dpkg.log`, `alternatives.log`, `btmp`, `lastlog`, `wtmp`, `faillog`. |
| `/var/spool` | `mail`, a link to `../mail`, which stays inside `/var`. |
| `/var/tmp` | Already read-write at the baseline. |
| `/var/run`, `/var/lock` | Links to `/run` and `/run/lock`. See below. |

Why reading it gives an agent nothing it shouldn't have:

- **SSSF writes nothing to `/var` outside `/var/tmp`.** The Containerfile's only `/var` write is the `rm -rf /var/lib/apt/lists/*`. At run time `sandbox_openshell.py` and `sandbox_remote.py` write to `/tmp` (`/tmp/sssf-in-<hex>`, `/tmp/sssf-obs-<hex>`), to `TMPDIR`, and to the baked folder. The Claude token and the ChatGPT token travel in the env frame on stdin and are never written to a file under `/var`.
- **The image is public.** It's built from `BASE_IMAGE` and public packages, with no secret baked in. The package database tells an agent which packages are installed, which it can already learn from `/usr` and `/etc`, both read-only at the baseline.
- **File modes still apply.** The agent is `ubuntu` (uid 1000), not root. Landlock only narrows what the modes allow, so root-only files (`passwords.dat`, `ldconfig`'s cache, `btmp`) stay closed.
- **Another exec's secrets are not there.** Env values are in `/proc/<pid>/environ`, which the baseline already covers under `/proc` and which `docs/designs/openshell-sandbox.md` records as refused between execs.

### The links out of `/var`

`/var/run` links to `/run` and `/var/lock` links to `/run/lock`. Landlock ties a rule to the folder it names and judges an open by where the opened file really is, after the kernel has resolved every link in the path. So `open("/var/run/x")` is judged as `/run/x`, which no rule covers, and is refused as it is at the baseline. A rule on `/var` does not make `/run` readable.

What changes: listing `/var` shows the two link names. `readlink` and `stat` are not among the accesses Landlock controls, so an agent could already read where a link points and stat a path under `/run` at the baseline. That is not new.

The planner knows of no other link under `/var` that leaves it in this image. `/var/spool/mail` stays inside. The engineer's proof lists them in a real VM (see "For the engineer").

## Threat model

Who: an agent, or any code it starts, inside an OpenShell VM. It runs as `ubuntu` (uid 1000) with no capabilities and can run any program in the image. This module is not used on the host, where the agent runs as the engineer's own user under agentsh; nothing here changes what a host agent can do.

What the agent can do after this change that it can't at the baseline:

- List `/var` and every folder under it that file modes let `ubuntu` list.
- Open for reading every file under `/var` that file modes let `ubuntu` read. At the baseline that was only `/var/log` and `/var/tmp`.

What it still can't do:

- Write, create, rename or delete anything in `/var` outside `/var/tmp`. `/var` is in `read_only`, and `read_write` is unchanged.
- Read anything in `/run` through `/var/run` or `/var/lock`.
- Reach any new host or use any new binary. The network rules are unchanged.

What this guard can't stop at its level:

- Anything file modes allow under a path the policy already covers. The policy names trees, not files.
- `stat` and `readlink` on any path. Landlock doesn't control them.
- A `conftest.py` in `/var/tmp` (or `/tmp`). pytest loads conftest files from every folder it walks, and any process in the VM can write there. A pytest run that is given a path under `TMPDIR` would load it. At the baseline the same is true for a path under `/tmp`, and for `/var/tmp` the run failed before it got that far. Every process in the VM is already agent-controlled, and the VM is the boundary, so this is no new reach. It is listed so a reviewer files it as an old hole.
- What OpenShell's own supervisor may keep under `/var` in a running VM. The planner can't see a VM. File modes still apply to it, and the engineer's proof looks.

## Bar

Against `53947df`, the change must:

- **Be red on `main`.** The new test `test_every_parent_of_tmpdir_is_covered_by_the_policy` fails on `53947df` for every roster kind, and the failure names `/var`.
- **Be green after.** The same test passes for `pi`, `claude` and `mixed` (the `KINDS` the policy tests already use).
- **Change nothing else in the policy.** `read_only` gains exactly `"/var"`, appended last. `/var/log` stays in the list. `read_write` stays `["/tmp", "/var/tmp", "/dev/null", <folder>]`. `include_workdir`, `landlock`, `version` and every network rule stay as they are. The three tests that pin the lists change only by that one entry.
- **Leave the image alone.** `containerfile()` is untouched, so the image tag doesn't change and no repo has to run `just openshell-setup`.
- **Keep every existing test passing on the host.**

It must never:

- Make any path under `/var` writable other than `/var/tmp`.
- Add `/`, `/run`, `/home`, or any other path to either list.
- Change `TMPDIR`.

The usual guard bar holds unchanged: no agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. This change touches none of those paths: the VM's files are a clone, and only commits come back to the host.

## Steps

Do them in this order, so the red run comes first.

### 1. Add the red test

File: `.claude/skills/sssf/tests/test_sandbox_openshell.py`.

Add this test right after `test_tmpdir_is_in_the_policy_and_outside_the_repo` (it ends near line 922), inside the `# --- TMPDIR:` section. `KINDS`, `F`, `roster`, `osh`, `sandbox_remote`, `Path` and `pytest` are already defined or imported in the file.

```python
@pytest.mark.parametrize("kind", KINDS)
def test_every_parent_of_tmpdir_is_covered_by_the_policy(kind):
    """A tool that lists a parent of TMPDIR must be allowed to. pytest lists every parent
    of a test file outside its rootdir, up to but not including `/` (run 12ec0782)."""
    fs = osh.policy(F, [], sandbox_remote.harnesses(roster(*KINDS[kind])))["filesystem_policy"]
    covered = [Path(p) for p in fs["read_only"] + fs["read_write"]]
    tmpdir = Path(osh.TMPDIR)
    assert tmpdir.is_absolute() and tmpdir != Path("/")
    below_root = [p for p in (tmpdir, *tmpdir.parents) if p != Path("/")]
    uncovered = [str(p) for p in below_root if not any(p == c or c in p.parents for c in covered)]
    assert uncovered == [], f"the policy covers no path at or above {uncovered}, so it can't be listed"
    assert "/" not in fs["read_only"] and "/" not in fs["read_write"]
    assert osh.TMPDIR in fs["read_write"] and "/var" not in fs["read_write"]
```

Run the file (command under "Verification"). The new test must fail three times, each with `['/var']` in the message. If it fails any other way, fix the test before going on. Put the failing output's last lines in the report.

### 2. Add the entry

File: `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`.

Change `READ_ONLY_PATHS` (lines 69-72) to:

```python
# The devices are read-only because nothing needs to write them. /dev/tty, /dev/ptmx,
# /dev/pts and the three std streams are left out on purpose; `>&2` works without them.
# /var is read-only so a tool can list TMPDIR's parent: pytest lists every parent of a test
# file outside its rootdir (run 12ec0782). Only /var/tmp, in read_write, can be written.
READ_ONLY_PATHS = ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero", "/dev/random",
                   "/etc", "/var/log", "/var"]
```

Change nothing else in the code. `TMPDIR`, `policy()`, `containerfile()` and both `_frame` calls stay as they are.

### 3. Update the module docstring

Same file, lines 13-14. Replace

```
Every exec gets `TMPDIR=/var/tmp`, which is on the VM's disk, and the policy allows it
read-write. The VM's size comes from the gateway's config, which `just openshell-setup` writes.
```

with

```
Every exec gets `TMPDIR=/var/tmp`, which is on the VM's disk, and the policy allows it
read-write. The policy allows `/var` read-only, so a tool can list `TMPDIR`'s parent:
pytest lists every parent of a test file outside its rootdir, and run 12ec0782's plan
check failed on `/var` before this. Nothing in `/var` but `/var/tmp` can be written, and
`/var/run` and `/var/lock` link to `/run`, which stays closed. The VM's size comes from the
gateway's config, which `just openshell-setup` writes.
```

### 4. Update the three pinning tests

File: `.claude/skills/sssf/tests/test_sandbox_openshell.py`. Each changes only by the one entry.

- `test_the_default_policy` (lines 205-206): the expected `read_only` becomes `["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero", "/dev/random", "/etc", "/var/log", "/var"]`. The `read_write` line stays.
- `test_the_image_holds_rsync_and_nothing_else_changed` (lines 382-383): the expected `osh.READ_ONLY_PATHS` becomes the same ten-entry list.
- `test_the_policy_differs_from_the_baseline_only_by_the_two_devices` (line 940): the expected `rest` becomes `["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log", "/var"]`. Keep the test's name and every other assertion, including `fs["read_write"] == ["/tmp", "/var/tmp", "/dev/null", str(F)]` and the network rule checks.

Do not touch any other test.

### 5. Update the docs

- `.claude/skills/sssf/references/config.md`, line 727 (the `--sandbox openshell` paragraph). After the sentence "`/tmp` is still writable for tools that ignore `TMPDIR`." add: "The policy also lets the VM's processes read `/var`, so a tool can list `TMPDIR`'s parent. pytest does that for a test file under `TMPDIR`, and failed there before (run 12ec0782). Only `/var/tmp` can be written in `/var`." Change nothing else on that line.
- `docs/designs/openshell-sandbox.md`, line 48 (decision 8, "The policy"). Change "the default read-only system paths, `read_write` on `/tmp`, `/var/tmp` and the baked parent folder" to "the default read-only system paths, `/var` read-only so a tool can list `TMPDIR`'s parent (run 12ec0782), `read_write` on `/tmp`, `/var/tmp` and the baked parent folder". Change nothing else.
- `docs/open.md`, line 63, the row that starts "A hub run in an OpenShell VM stopped at verify". Replace everything from "What is left is the plan-check cause:" to the end of the first cell with:

  "The last cause was the plan check: run 12ec0782 (2026-11-25) failed its plan check, because the check made its scratch folder with `mktemp -d` under `/var/tmp`, pytest lists every parent of a test file outside its rootdir, and the VM's policy refused listing `/var`. Guard run 2ebbe80a (2026-11-26) fixed it by adding `/var` to the policy's read-only paths; `/var/tmp` is still the only writable path there. The fix is in. What closes this row is the engineer's proof in a throwaway VM, which has not been run yet: 12ec0782's plan check command passes, `ls /var` works and a write directly in `/var` is refused, reading through `/var/run` reaches nothing in `/run`, and the six pi tests pass. After that a hub `simple-sdlc --sandbox openshell` run can pass verify."

  Keep the row's other cells (`Bug`, `P1`, `Medium`, `Ready`, `2026-11-26`, the link) as they are. The old sentence "The fix is to keep plan checks' scratch folders inside the repo's tree." goes, since the fix is in the VM instead.

There is no CHANGELOG. `README.md` and the skill's `SKILL.md` don't mention the policy's paths. `docs/research/pi-under-openshell.md` line 68 lists the read-only paths as they were when that research was done (it already lacks `/dev/zero`); it is a dated record, so leave it.

For the documenter: no write-up in `app_docs/` names the read-only list or `/var/tmp`, so none goes out of date.

## Files

| File | Change |
| --- | --- |
| `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` | `"/var"` appended to `READ_ONLY_PATHS`, its comment, the docstring's `TMPDIR` lines. |
| `.claude/skills/sssf/tests/test_sandbox_openshell.py` | One new test, three expected lists gain `"/var"`. |
| `.claude/skills/sssf/references/config.md` | Three sentences on line 727. |
| `docs/designs/openshell-sandbox.md` | One clause on line 48. |
| `docs/open.md` | The P1 row on line 63. |

The first two are in `protected_files`, and `guard_builder`'s `writes` names both. `config.md` is under `.claude/skills/sssf/`, which its `writes` also names. The two under `docs/` are not protected.

## Verification

The builder runs under agentsh and can't start a VM, so run only this file. From the repo root:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_sandbox_openshell.py -m 'not pushes and not host_only'
```

Judge it by its exit status.

1. After step 1 only: exit status 1, with exactly three failures, all `test_every_parent_of_tmpdir_is_covered_by_the_policy[...]`, each naming `['/var']`. That is the red run on the baseline's code.
2. After steps 2 to 4: exit status 0, with 107 passed (104 at the baseline plus the three new cases).
3. `git diff 53947df -- .claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` shows only the `READ_ONLY_PATHS` lines, its comment and the docstring. No line of `policy()`, `containerfile()` or `TMPDIR` is in the diff.

The whole skill suite is left to verify, which runs on the host.

## Out of scope

Holes and limits this change leaves open, so a reviewer files them as old ones:

- **The planner's prompt and `plan_checks.py`.** A check that writes to a temp folder is fine once the VM can list its parents.
- **`/tmp`'s size, and how much temp the skill suite writes.** That is `docs/open.md`'s row on pytest temp.
- **Listing `/`.** pytest never lists it (see "What the planner checked"). Another tool that lists `/` still fails in the VM, as at the baseline.
- **A planted `conftest.py` in `/var/tmp` or `/tmp`.** See the threat model. Old, and inside the VM's boundary.
- **`stat` and `readlink` outside the policy's paths.** Landlock doesn't control them. Old.
- **The redundant `/var/log` entry.** `/var` now covers it. It stays, so the lists change by one entry only.
- **What OpenShell itself keeps in `/var` or `/run` in a VM.** Not visible from the host; the engineer's proof looks.
- **Whether OpenShell accepts a read-only parent with a read-write child.** Landlock does. If OpenShell doesn't, the sandbox fails to start, and the engineer's proof finds that before any hub run depends on it.

## For the engineer

Nothing here is a builder step.

- **The hub's own install.** `adws/adw_modules/sandbox_openshell.py` is the hub's installed copy and is protected. After the merge, update the hub's SSSF install as after 46bee4c, or hub runs keep the old policy. No `just openshell-setup` is needed: the image tag doesn't change, and the policy file is written per run.
- **The VM proof**, in a throwaway OpenShell VM made with the new policy:
  - 12ec0782's plan check command passes (a test file under `mktemp -d`, run with `pytest -c pytest.ini <folder>/src/test_kept.py` from the repo).
  - `ls /var` works, `touch /var/x` is refused, and `touch /var/tmp/x` works. The last one also proves OpenShell takes a read-write child under a read-only parent.
  - `ls /var/run/` and `cat` of a file under `/var/run` are refused, the same as `ls /run`.
  - `find /var -xdev -type l -exec ls -l {} +` shows no link that leaves `/var` other than `run` and `lock`.
  - `ls -la /var/lib /var/cache` shows nothing of OpenShell's that `ubuntu` can read and shouldn't.
  - The six pi tests from guard run 7e8490c2 pass.
- Then a hub `simple-sdlc --sandbox openshell` run can pass verify, and the P1 row in `docs/open.md` can close.
