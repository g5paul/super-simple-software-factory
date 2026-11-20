# Plan: the build gate passes a file the call deleted, judged against the call's start commit

## Problem

`diff_matches_claims` in `.claude/skills/sssf/templates/adws/adw_modules/gates.py` (line 63) passes a claimed path only when `sandbox.current().exists` says it exists. A builder that runs `git mv` and lists both paths, or deletes a file and lists it, fails with "claimed changed file does not exist". Since 411114a it gets one correction, and the only way to pass is to drop true entries from `changed_files`. An unlisted project's OpenShell run ended this way on 2026-11-19, for $1.40. The gate runs after every `builder` and `ticket_builder` call in every chain, and no test covers it.

Guard run caf45b46 (branch `sssf/caf45b46`, not merged) read `git ls-tree -r HEAD` at gate time. `agents.py` runs the gates before `permissions.undo_commits`, so `HEAD` is whatever the agent left: a file the agent created, committed and removed passed as deleted, and a file it removed with `git rm` and committed failed. This plan replaces that rule. It reuses caf45b46's command isolation, its odd-name cases and most of its tests.

## Baseline

`main` at `69e5b03` ("Amend the rename-gate brief after caf45b46's bypass, and track the forged-tree hole"). Every finding is judged against that commit. At the baseline:

- The gate sends, per claim, `exists(path)` and then, if it exists, `size(path)`. It runs no git command.
- A missing path fails with the note `claimed changed file does not exist`.
- `agents.execute` records `head_before = permissions.head(run)` before the first send (line 339). It is a dict, `{"sha", "branch", "refs"}`. The gates run at line 361 as `gate(envelope, run)`, before `permissions.undo_commits` (line 420).
- `agents.py` skips every gate when `permissions.git_dir_changed` reports a watched part of the git directory changed. `refs/` and `objects/` are not watched.

`gates.py`, `agents.py`, both builder prompts and `tests/test_builder_brief.py` are the same at `69e5b03` as at `cd04bdf`, caf45b46's base.

## Design

### The rule

A claimed path passes when either holds:

1. It exists, as today. Note: `exists, <size>`.
2. It does not exist, and its exact string is the name of a file in the tree of the commit the call started from. Note: `deleted in the working tree`.

Anything else fails with today's note, `claimed changed file does not exist`.

"Gone" is the `exists` call the gate already makes. The git read answers only "was it in the start commit". The index and `HEAD` are never read, so `git rm`, `git mv`'s source, a plain `rm`, a plain `mv`'s source, and any of those followed by a commit all judge the same.

### How the gate receives the start commit

`diff_matches_claims` gains one optional parameter: `def diff_matches_claims(envelope, run, start: str | None = None)`.

`agents.execute` passes it. A new private helper in `agents.py`, `_judge(gate, envelope, run, start)`, calls `gate(envelope, run, start=start)` when `inspect.signature(gate).parameters` holds a parameter named `start`, and `gate(envelope, run)` otherwise. A callable whose signature can't be read (`TypeError`, `ValueError`) gets the two-argument call. `execute` passes `head_before["sha"]`, or `None` when it is empty.

- The value lives in `execute`'s local `head_before`, in SSSF's process, taken before the agent ran. The agent can move `HEAD`, a branch or any ref and the value doesn't change.
- No module-level state, no `global`, and nothing new on `run`. `_judge` and one regex constant are the only new names.
- Every other gate, an inline one-off and a legacy gate that returns a list keep the two-argument call. No chain script changes: each still passes `gates.diff_matches_claims` itself.
- All gate attempts of one call get the same `start`, since `head_before` is taken once per call.

### With no start commit

`start` is used only when it is a whole object id: `re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", start)`. With `None`, `""`, a short id, or a name such as `HEAD` or `main`, the gate runs no git and every missing claim fails with today's message. That is the baseline's behaviour, so a caller that passes no `start` (a test, an old `agents.py` beside a new `gates.py`) is exactly as strict as `69e5b03`. A repo with no commit has no start tree, so nothing in it can be deleted. `permissions.snapshot` refuses such a repo before the gates anyway.

When the git read exits non-zero (not a repo, the id names no object, a partial clone lacks the tree), nothing is deleted and the claim fails as today.

### The git command

```
env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C \
  git --no-replace-objects -c core.fsmonitor= -c core.hooksPath=/dev/null \
  -c core.commitGraph=false -c advice.graftFileDeprecated=false \
  ls-tree -r -z --name-only --full-tree <start's full id>
```

- The `env ...` words are `permissions._GIT_ENV`, imported. `-c core.fsmonitor= -c core.hooksPath=/dev/null` is `utils.GIT_NO_PROGRAMS`, imported. The rest matches `permissions._REF_READ`.
- **Why `ls-tree`.** It prints every file of one commit and reads no index and no work-tree file, so no filter, fsmonitor, hook or submodule is reached and the index is not rewritten. `git status` and `git diff` read the work tree and answer relative to `HEAD` or the index, which the agent controls.
- **The full id.** Git reads a 40- or 64-hex string as an object id before it looks for a ref. Tried on git 2.43.0 while writing this plan: with a branch and a tag both named as the start id and pointing at another commit, and a replace ref from the start commit to that other commit, the command above printed only the start commit's files. Git's "refname is ambiguous" warning goes to stderr, which the gate doesn't read. A plain `git ls-tree -r <id>` in the same repo listed the other commit's files.
- **Odd bytes.** With `-z` git prints each name raw and ends it with a NUL: no quoting, no escaping. The gate calls `run(..., text=False)`, decodes with `os.fsdecode`, and splits on `"\0"`. A space, a non-ASCII letter and a newline arrive as themselves.
- **No claim is an argument.** The command takes no path, so a claim such as `--output=x` can't become an option.
- `-r` lists files, never a folder. `--full-tree` prints names from the repo root.
- No `cwd`: the read uses the process's working directory, the base `exists` uses, on the host and in a VM.
- It goes through `sandbox.current().run`, so a VM run asks the VM's clone, where the agent's tree is. `permissions.head` reads the id through the same sandbox.
- It runs at most once per gate call, and only when some claimed path doesn't exist.

### How a claim is matched

The raw claim string is looked up in the set of names git printed. No `Path`, `normpath`, `resolve`, `strip` or case folding.

| Claim, where `x` and `src/x` are real deleted files | Judged |
|---|---|
| `x` | passes, `deleted in the working tree` |
| `./x` | git prints `x`: fails |
| `src//x` | git prints `src/x`: fails |
| `x/../y`, `src/../x` | git prints no name with `..`: fails |
| an absolute path, inside or outside the repo | git prints names from the root only: fails |
| `../x` | never printed: fails |
| a folder whose files were all deleted | `-r` prints files only: fails |
| git's quoted spelling, such as `"\303\251.txt"` | `-z` prints the raw name: fails |

Each failing row fails with today's message. Any of them that exists passes on `exists`, as at the baseline.

### Code

`.claude/skills/sssf/templates/adws/adw_modules/gates.py`:

```python
import os
from .permissions import _GIT_ENV
from .utils import GIT_NO_PROGRAMS

# A whole SHA-1 or SHA-256 id. Git reads one as an object, never as a ref name.
_FULL_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _files_at(start) -> set[str]:
    """Every file in the commit `start`, named from the repo root as git names it.
    Empty when `start` isn't a full id or git can't read it."""
    if not isinstance(start, str) or not _FULL_ID.fullmatch(start):
        return set()
    done = sandbox.current().run(
        [*_GIT_ENV, "git", "--no-replace-objects", *GIT_NO_PROGRAMS,
         "-c", "core.commitGraph=false", "-c", "advice.graftFileDeprecated=false",
         "ls-tree", "-r", "-z", "--name-only", "--full-tree", start], text=False)
    if done.returncode != 0:
        return set()
    out = done.stdout
    return set((os.fsdecode(out) if isinstance(out, bytes) else out or "").split("\0")) - {""}


def diff_matches_claims(envelope: EnvelopeBase, run, start: str | None = None) -> GateReport:
    report = GateReport()
    at_start = None
    for f in getattr(envelope, "changed_files", []):
        p = Path(f)
        if sandbox.current().exists(p):
            report.check(f, True, f"exists, {_size(p)}")
            continue
        if at_start is None:
            at_start = _files_at(start)
        deleted = f in at_start
        report.check(f, deleted, "deleted in the working tree" if deleted
                     else "claimed changed file does not exist")
    return report
```

- Don't catch `OSError` or `TimeoutExpired`: an unreachable VM raises from `exists` today, and the read behaves the same.
- The docstring says: every claimed path exists, or is a file in the start commit that is gone from the working tree; `start` comes from `agents.execute`, never from the repo at gate time, and why; names match exactly.
- The module docstring's line on a gate's shape gains: a gate that names a `start` parameter also gets the id of the commit the call started from.
- `permissions.py` imports only `sandbox` and `data_types`, so there is no import cycle. Nothing else in `gates.py` changes.

`.claude/skills/sssf/templates/adws/adw_modules/agents.py`:

- Add `import inspect` if it isn't there, and `_judge` under "internals", with a docstring saying why `start` comes from here.
- Line 362 becomes `report = _as_report(_judge(gate, envelope, run, start))`, with `start = (head_before.get("sha") or None) if isinstance(head_before, dict) else None` set once before the gate loop.
- Change nothing else in `execute`. The gates stay before `undo_commits`.

## Steps for the builder

1. **Tests first.** Add `.claude/skills/sssf/tests/test_claims_gate.py`. Start from caf45b46's file: `git show sssf/caf45b46:.claude/skills/sssf/tests/test_claims_gate.py`. Keep its `git` helper, the `Recording` double (a `sandbox.Local` subclass that records `run`, `exists` and `size`), the `repo` fixture, the `Vm` double and the made-up-path makers. Change these:

   - The `repo` fixture records `start = git rev-parse HEAD` after its commit, and the `gate` helper calls `gates.diff_matches_claims(BuildOutput(...), None, start=start)`.
   - `COMMAND` ends with the start id instead of `HEAD`. Keep it a literal list, so a later weakening of `permissions._GIT_ENV` fails here.
   - Drop the tests that only made sense for `HEAD` (`test_no_commit_yet` as written).

   The tests. Each asserts `report.passed`, the notes, and for a failure `report.violations == [f"{claim}: claimed changed file does not exist"]`:

   - **Renames pass**, `git mv` and plain `mv`, both paths claimed: the new one notes `exists, ...`, the old one `deleted in the working tree`.
   - **Deletes pass**, `git rm` and plain `rm`.
   - **Made-up paths fail** (parametrized): never existed; an untracked file created then removed; a file removed and committed before the start commit was recorded; `../outside.txt`; an absolute path outside the repo; the absolute path of a real deleted file; `./gone.txt`, `src//a.txt`, `src/../gone.txt`; a folder whose files were all removed; git's quoted spelling of a deleted non-ASCII name.
   - **Commit-then-remove fails** (parametrized over `git commit` and `git commit-tree` with `git update-ref HEAD`): after the start id is recorded, create `fake.txt`, commit it either way, remove it, claim it. It fails with today's message. `undo_commits` is never called in this test.
   - **Delete-then-commit passes**: `git rm gone.txt`, commit, claim `gone.txt`.
   - **A moved HEAD changes nothing** (parametrized). Setup: commit `fake.txt`, record that commit as `other`, `git reset -q --hard <start>`, then delete `gone.txt`. Take the verdicts for the claims `gone.txt`, `fake.txt`, `keep.txt`, `never.txt` as `(item, ok, note)` tuples. Move things with plumbing that leaves the work tree alone, one case each: `update-ref HEAD <other>`; `update-ref refs/heads/main <other>`; `update-ref --no-deref HEAD <other>`; `symbolic-ref HEAD refs/heads/elsewhere`; `replace <start> <other>`; `replace <start>^{tree} <other>^{tree}`; `update-ref refs/heads/<start> <other>` with `update-ref refs/tags/<start> <other>`. For a replace case, first assert a plain `git ls-tree -r --name-only <start>` now lists `fake.txt`. Then the verdicts equal the ones taken before the move.
   - **Odd names pass** (parametrized): a tracked file named with a space, with `é`, and with a newline, removed and claimed by its real name.
   - **One read per call**: one real deletion and one made-up path make exactly one `run`.
   - **Nothing else changes**: with claims that all exist, the double saw no `run`, and its calls are exactly `exists` then `size` per claim, with or without `start`.
   - **The exact command**: the one `run` equals `COMMAND`, with `text=False` and no `cwd`.
   - **No start commit** (parametrized over no argument, `None`, `""`, a 7-character id, `"HEAD"`, `"main"`, `"--help"`): a deleted tracked file fails with today's message and the double saw no `run`. With 40 zeros, and in a folder that isn't a repo with a real-looking id, one `run` is sent, the claim fails with today's message and nothing raises.
   - **The VM is asked, not the host**: caf45b46's `Vm` double, not a `Local`: `exists` is `False` for every path, `size` raises, `run` returns `CompletedProcess(cmd, 0, b"gone.txt\0", b"")`. In a host repo where `gone.txt` still exists, `gone.txt` passes as deleted and `other.txt` fails. Assert the command the double received equals `COMMAND`, start id included.
   - **A partial clone fetches nothing** and **no planted program runs**: caf45b46's two tests, with the start id.
   - **`execute` hands over the start commit.** Import the `world` fixture and `git` from `tests/test_failed_call_rollback.py`, as `tests/test_builder_brief.py` imports from its neighbours. It drives the real `agents.execute` on a real repo with the config SSSF ships, `templates/sssf.config.yaml`.
     - A spy gate `def spy(envelope, run, start=None)` and a two-argument gate, in `world.gates`. The turn commits a new file before it answers. The spy received `world.base`, not the new `HEAD`, and the two-argument gate was called.
     - The turn creates `fake.txt`, commits it, removes it, and reports `changed_files: ["fake.txt"]`, with `world.gates = [a wrapper that records world.calls["undo"] and returns gates.diff_matches_claims(envelope, run, start=start)]`. `execute` raises `agents.GateFailure` naming `fake.txt: claimed changed file does not exist`, the `gate_fail` event holds that violation, and the wrapper saw `undo == 0`: the verdict was given before `undo_commits` ran.
   - **The prompts say what to list** (step 3), for `builder` and `ticket_builder`.

   Run the file and see the new behaviour fail before step 2. If a test fails only under agentsh, mark it `host_only` and say so in your notes.

2. **The gate and the hand-over.** Edit `gates.py` and `agents.py` as under "Code".

3. **The prompts.** In `.claude/skills/sssf/templates/prompt_engineering/builder/user.md` and `.claude/skills/sssf/templates/prompt_engineering/ticket_builder/user.md`, add this line as its own paragraph right after the Report's JSON block, where caf45b46 put it (`git diff cd04bdf sssf/caf45b46 -- .claude/skills/sssf/templates/prompt_engineering`):

   > `changed_files` lists every path your change touched, from the repo root, as git names it: each file you added or edited, each file you deleted, and both the old and the new path of a rename.

   It says what to report. It grants nothing.

   `.claude/skills/sssf/tests/test_builder_brief.py` pins a sha256 of each prompt in `MAIN`. Replace the two `user.md` values with what `sha(baseline(rel))` gives after the edit, and fix the comment above `MAIN`. Placed as caf45b46 placed it, they are `cb58ab37aeb19f3fb542863dc3eef46989e6ae30b0fbfb5de30526ac68169d3d` and `06815ba7cd132ba1f9a2d336d57766c4f36b7e3cccbadb073ed20242fc4fef36`. Compute them, don't trust these.

4. **Docs.**
   - `.claude/skills/sssf/cookbooks/create_adw.md`, step 4 (line 34): after `diff_matches_claims` add "(passes a claimed path that exists, or a file of the commit the call started from that is gone from the working tree, such as a deleted file or a rename's old path, matched by its exact name)".
   - `.claude/skills/sssf/cookbooks/update_modules.md`, "Adding a gate" (line 88): one sentence. A gate that names a `start` parameter also gets the id of the commit the call started from, which SSSF recorded before the agent ran.
   - `docs/concepts.md` line 279: replace "`changed_files` matches what git sees changed", which was never true, with "every path in `changed_files` exists, or is a file the call deleted or renamed away".
   - `docs/open.md`: delete the row that starts "The build gate fails a correct build that renames or deletes a file" (line 80).
   - `docs/open.md`: add one row to the `## SSSF` table, in its format: "`git_helper.changed_files()` slices each `git status --porcelain` line at 3, so a staged rename reads as `old -> new`, and a name git quotes keeps its quotes and escapes. Nothing in `templates/adws/` calls it today. Fix: read `--porcelain -z`, or delete the function", `Bug`, `P4`, `Low`, `Ready`, `2026-11-19`, empty Background. Leave `git_helper.py` alone.
   - `.claude/skills/sssf/references/config.md` needs no edit. Line 244 ("before any gate runs git") and line 700 ("SSSF's git ignores replace refs and `info/grafts`") stay true.
   - No CHANGELOG exists.

5. **Don't touch** `adws/`, `app_docs/`, `permissions.py`, `utils.py`, `git_helper.py` or any chain script. Don't commit or stage.

For the documenter: no `app_docs/` write-up describes `diff_matches_claims`, so none goes out of date.

## Threat model

The agent writes the `changed_files` list, every file in the work tree, and, as the engineer's own user, anything that user can write. That includes the git directory: `HEAD`, every ref, `refs/replace/`, `objects/`, `objects/info/alternates`, a commit-graph. In a VM it owns the machine the read runs on.

The gate checks that the builder's report is consistent with the tree. It is not a boundary. At the baseline an agent passes any made-up claim by creating a file at that path.

What the change must not add, and how it avoids it:

- **A verdict the agent sets by moving a ref.** The start id is SSSF's own, recorded before the agent ran and handed over as an argument. It is a full object id, so no ref named like it is read. Replace refs, grafts and the commit-graph are off.
- **Agent code run by SSSF's git.** The read loads no index and no work-tree file. `agents.py` still skips every gate when a watched part of the git directory changed.
- **A fetch.** A missing object makes the read fail. It starts no transport.
- **Claims on a command line.** No claimed string is passed to git. The output is split on NUL and used for set membership only.

What this gate can't stop at its level:

- **A forged object.** An agent that writes a loose tree object over the id of one of the start commit's subtrees in `.git/objects`, or serves one through `objects/info/alternates`, makes `ls-tree` list a file that was never there, so a made-up claim passes. Git checks the root tree's and the commit's hashes, not a subtree's. This holds on the host as in a VM. It is accepted: the gate isn't a boundary, and the same agent passes the same claim at the baseline by creating the file. The object-store hole in `permissions` has its own `docs/open.md` row.
- A VM agent that replaces the `git` or `env` program can make the read, and `permissions.head` before it, say anything.
- An agent that deletes a file it should have kept passes the gate by claiming it. Whether the deletion is allowed is `permissions`' and the reviewer's job.
- A false claim that passes stays in the envelope. Nothing judges the claims again after `undo_commits`.

## Bar

Against `69e5b03`:

- **Renames pass**: `git mv` and plain `mv`, both paths claimed.
- **Deletes pass**: `git rm` and plain `rm`.
- **Made-up paths still fail, with today's message**: never existed; untracked and gone; deleted in an earlier commit; outside the repo as `../x` or absolute; a spelling git doesn't print, such as `./x` or `src//x`.
- **Commit-then-remove fails** with today's message, by `git commit` and by `git commit-tree` with `git update-ref HEAD`, judged before `undo_commits` runs.
- **Delete-then-commit passes.**
- **A moved `HEAD`, branch or replace ref changes no verdict.**
- **Odd names pass** by their real name: a space, a non-ASCII letter, a newline.
- **The VM is asked, not the host**, and a test asserts the command it received, with the start commit's full id.
- **Nothing else changes.** Every existing test passes. A call whose claims all exist sends exactly the baseline's commands and no git. A gate call with no start commit judges as the baseline does.
- **Must never**: keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't. The gate writes nothing: not the tree, not the index, not the object store, not a ref.

## Out of scope

Old holes and known edges this change leaves open:

- The gate does not check that every changed file is claimed, or that claims and the diff match both ways.
- A claim passes if the agent creates a file there (baseline). Content is not checked.
- The claims are not judged again after `undo_commits`.
- The forged-object case above, and `permissions.enforce`'s own (`docs/open.md`).
- A file of the start commit that was already gone from the working tree when the call began passes as deleted. SSSF commits or rolls back between calls, so this needs a tree left dirty by hand.
- A tracked symlink whose target is missing reads as not existing, since `exists` follows links, so claiming it now passes as deleted. At the baseline it failed. A submodule's path in the start commit judges like a file.
- In a sparse checkout, a file the checkout leaves out passes as deleted. SSSF makes no sparse worktrees.
- In a partial clone that lacks the start commit's trees, a real deletion fails as at the baseline, because the read fetches nothing.
- A deleted file whose name isn't valid UTF-8 can be claimed only with the lone-surrogate string `os.fsdecode` gives for it. Through a VM, whose output arrives as text, such a name may not match.
- If the ADW's working directory is a subfolder of the repo, `exists` judges against that folder and git against the root. Every chain runs from the root.
- A forged commit-graph has no test. The option that turns it off is pinned by the exact-command test.
- `git_helper.changed_files` and its `line[3:]` slice: an `open.md` row only.
- The other open rows.

## Verification

From `.claude/skills/sssf`, with light runs, since the agentsh event store slows under load:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_claims_gate.py tests/test_plan_gate.py tests/test_builder_brief.py tests/test_failed_call_rollback.py tests/test_sandbox.py -m 'not pushes and not host_only' -q
```

- `tests/test_claims_gate.py` is the bar. Judge by exit status.
- `tests/test_failed_call_rollback.py` drives `agents.execute`, which now calls gates through `_judge`.
- `tests/test_sandbox.py` fails if `gates.py` starts a process around the sandbox.
- `tests/test_builder_brief.py` pins the prompt texts.
- From the repo root, the closed row is gone: `! grep -q "The build gate fails a correct build that renames or deletes a file" docs/open.md`.
- Leave the whole suite to the verify phase. No test starts a real VM or a real agent. No new tool is needed: git and the `uv run --with` packages above are what the suite already uses.

## For the engineer

These paths are protected and not in the builder's `writes`:

- `adws/adw_modules/gates.py`, `adws/adw_modules/agents.py`, `adws/adw_data/prompt_engineering/builder/user.md` and `adws/adw_data/prompt_engineering/ticket_builder/user.md` are the hub's own installed copies. They keep the old behaviour until the hub's install is updated to the merged commit, as af5d068 did for 411114a. A half-updated install is as strict as today: a new `gates.py` under an old `agents.py` gets no start commit, and an old `gates.py` under a new `agents.py` names no `start` parameter.
- Projects get the change on their next `just sssf-update`.
- Branch `sssf/caf45b46` is set aside. Its spec and tests were read for this plan. Delete the branch once this run merges.
