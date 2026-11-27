# Verify a checkout of the verified tree, and bind each `wrote` path to its blob

Guard change, run af37b9f1. It follows `specs/1befc519_verified-tree-binding.md`, whose binding rule (bound from the first round, ended only by `sandbox.verify_done()`) is in the code and stays. This run closes the two P2 rows of `docs/open.md` the bypass-reviewer filed on 2026-11-27 against 1befc519: H1 ("A verify VM checks bytes a checkout of the committed tree doesn't have") and O1 ("A file the chain writes itself is passed by its path alone").

All paths below that start with `templates/`, `tests/` or `references/` are under `.claude/skills/sssf/`; each is written in full once in its heading.

## Problem

Checked against the code at the baseline:

1. **H1.** `Remote._judge_patch` (`.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`) applies the build VM's patch to the last sync in a temporary index (`apply --cached`) and returns that tree. `Remote.open_verify` then clones the last sync into a fresh VM and runs `git apply --binary --whitespace=nowarn -` on the working files. `git apply` writes each file under the attributes the clone has on disk, which are the last sync's. When the patch adds or changes `.gitattributes`, the files on disk are not what a checkout of the verified tree gives. Confirmed in scratch repos while planning: with a patch that adds `app.py ident` and `*.sh eol=crlf` to `.gitattributes` and a `# $Id$` line to `app.py`, the baseline's apply leaves `# $Id$` and LF in `run.sh`; a clean clone of the patched tree has `# $Id: <blob id> $` and CRLF in `run.sh` (a file the patch did not touch).
2. **O1.** `commits_out` drops every `wrote` path from its comparison with the verified tree (`n not in wrote`), and `permissions.judge_commits` passes any change at one (`or path in wrote`). Only `adw_tickets.py` passes `wrote` (`wrote=[ticket.path]` in each `tNN_commit`). `tickets.mark_done` reads the ticket with `sandbox.current().read_text`, which under a VM is the build VM's disk, and writes it back there. A process the builder left running can rewrite the ticket before the read or after the write, and the commit lands it.

## Threat model

The builder runs arbitrary code in the build VM as the VM's user and may leave a process running. Everything on that VM's disk, in its index and in its object store is the agent's word at every moment, including between two host calls (between `mark_done`'s read and write, and between its write and the commit).

What the agent cannot reach: the host process, the host's object store except through a bundle the host unbundles and hashes itself, and the fresh verify VM before the checks start (no agent starts there; the checks themselves are the agent's code and run there).

So:

- The bytes verify checks must come from the tree the host built, checked out by git in the fresh VM, and the VM's tree id is compared with the host's before any file is written.
- The bytes the chain adds to a commit after verify must be chosen on the host: the text comes from the host's copy of the green tree, and the host hashes it. The VM's disk is only where the chain puts them. The commit that comes back is held to that hash.

What this guard can't stop at its level:

- Under `sandbox: none` the agent runs as the engineer's own user and there is no second machine. Nothing binds its tree, so a left process can rewrite the ticket before `mark_done` reads it and the rewrite lands.
- The checks are the agent's code. In the verify VM they can do anything to that VM, which is deleted after the round.
- The reviewer agents read the build VM's tree, not the verified one.

## Baseline

`main` at `70a4073` ("Add brief to verify a checkout of the verified tree and bind wrote blobs"). Its guard code is the merge `4a9eec4` (guard runs dafc21f0, 3d9f277f and 1befc519); the commits after it change only the hub's install, a proof record and this run's brief. Every finding is judged against `70a4073`.

## Bar

Must do, where the baseline does otherwise:

- **Verify checks a checkout of the verified tree.** In the verify VM, after a non-empty patch, the working files are what a clean checkout of `_judge_patch`'s tree gives under that tree's own `.gitattributes`. The `ident` plant and the `eol=crlf` plant go red in the verify VM, as they do in a clean checkout.
- **A tree mismatch fails the round.** If `git write-tree` in the verify VM is not the host's tree id, no check runs and the round fails (`SandboxFailed`).
- **A `wrote` path passes only with its blob.** `wrote` is a map from path to blob id. A path passes `judge_commits` and the comparison in `commits_out` only when the commit holds exactly that blob there as a regular file. Any other content, a deletion, a symlink or a gitlink at that path is judged like any other path, and `commits_out` refuses it when it differs from the verified tree.
- **Under a VM the ticket's text starts from the green tree.** A rewrite before `mark_done` reads does not land: the commit holds the green tree's ticket with only its Status line changed. A rewrite after `mark_done` writes is refused as a `PermissionBreach`, nothing of it lands, and the branch stays at the last sync.
- **A local commit** (`check_local_commit`) with a `wrote` path whose blob isn't the one passed is judged like any other path.

Must never do:

- No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise.
- No decision rests on what the build VM reports. The verified text and the blob id are read and computed on the host.
- Nothing an agent may do changes: no edit to the VM's policy, any prompt, any rule file, `templates/sssf.config.yaml`, or any agent's `writes`.
- A real run is unchanged: an ordinary change, a rename, a deleted file, a binary file and a `.gitattributes` change that alters no file's bytes each pass verify and land. A tickets chain marks each ticket resolved and lands it. In the verify VM, `HEAD` and the index are still the last sync, with the patch as uncommitted work, as on the baseline.
- An empty patch still checks the last sync (the fresh clone, untouched).
- No new module-level state.
- Every existing test passes on the host, except the four call sites named in step 6 that pass `wrote` as a list or a set, which are rewritten to pass a map.

Two accepted changes against the baseline:

- A caller that passes `wrote` as a list, tuple or set gets a `TypeError` before anything is staged. A chain a repo wrote itself must pass a map. `references/config.md` says so.
- A ticket file whose bytes git's clean conversion changes on `git add` (CRLF line ends under a `text` attribute, an expanded `$Id: … $`) is committed with a blob other than the one the chain hashed, so its commit is refused where the baseline landed it. Tickets are LF Markdown; this fails closed and the refusal names the path.

## Design choices

**H1: apply to the index in the VM, compare the tree id, then check every file out from the index.** The other way, bringing the host's commit into the VM, needs a ref on the host to bundle it from (the baseline promises `_judge_patch` moves no ref) and a second transfer. The patch applied to the last sync's index gives the same tree by content hash, and `git write-tree` in the VM against the host's id proves it. The brief's `git rm --cached -r -q . && git reset --hard` resets to `HEAD`, which is the last sync, so it would need a commit in the VM and would move the VM's `HEAD`, which the baseline's checks may read.

The script, run once in the fresh clone with the patch on stdin (`$1` the last sync, `$2` the host's tree id):

```sh
set -e
git rm -r -q --ignore-unmatch .
git read-tree "$1"
git apply --cached --binary --whitespace=nowarn -
got=$(git write-tree)
if [ "$got" != "$2" ]; then echo "the verify VM built tree $got, not $2" >&2; exit 3; fi
git checkout-index -f -a
git reset -q
```

Why each line:

- `git rm -r -q --ignore-unmatch .` removes every tracked file and emptied folder from the fresh clone, so no file written under the old attributes stays, a file the patch didn't touch included.
- `git read-tree "$1"` and `git apply --cached` build the index the same way the host's `_judge_patch` does.
- The `write-tree` comparison is how the round knows the VM's tree is the host's. It runs before any file is written.
- `git checkout-index -f -a` writes every file from that index into the now empty folder. Git reads `.gitattributes` from the index for a checkout, so each file is written under the verified tree's own attributes. Confirmed while planning: the result is identical (`diff -r`) to a clean clone of the patched tree, for `ident`, for `eol=crlf` on an untouched file, for a deleted file and for a binary file.
- `git reset -q` puts the index back at `HEAD` (the last sync), so `git status` and `git diff` in the verify VM read as on the baseline: the patch is uncommitted work.

**O1: the text from the host's green tree, the blob id from the host's `git hash-object`.** `mark_done` returns the map it wants bound, so the chain can't pass a path without a blob.

**`sandbox: none`.** `mark_done` keeps reading the local file: there is no green tree on the host to start from and no binding. It still hashes the text it writes and returns the map, so `check_local_commit` passes the ticket's path only with that blob. That is tighter than the baseline at no cost; a rewrite before the read still lands, as the threat model says.

## Changes

Write the tests first (step 6) and run them against the unchanged code, so each red is seen.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

- Add one helper near `_inside`:

  ```python
  def wrote_blob(wrote, path: str, status: str, mode: str, sha: str) -> bool:
      """True when the chain wrote `path` and the commit holds exactly that blob there,
      as a regular file. `wrote` maps a path to the blob id the chain hashed."""
      return status != "D" and mode in ("100644", "100755") and bool(sha) and wrote.get(path) == sha
  ```

- Add `def wrote_map(wrote) -> dict[str, str]`: `{}` for `None` or an empty value; a `collections.abc.Mapping` is returned as a `dict`; anything else raises `TypeError("wrote maps each path the chain wrote to its blob id; a list of paths binds nothing")`.
- `judge_commits`: default `wrote=None`; first statement after the docstring `wrote = wrote_map(wrote)`; replace `or path in wrote` with `or wrote_blob(wrote, path, status, mode, new)`. The secret rule above it is not touched: a `wrote` path that is a secret stays refused.
- `check_local_commit`: default `wrote=None`; pass `wrote=wrote_map(wrote)` in place of `wrote=list(wrote)`. Call `wrote_map` before the `try`, so a wrong type is a `TypeError`, not a refusal.
- Module docstring near line 57 and both functions' docstrings: a `wrote` path passes only when the commit's blob there is the one the chain passed.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

**`open_verify`**: replace

```python
if patch:
    inner._must("git", ["apply", "--binary", "--whitespace=nowarn", "-"],
                cwd=str(self.tree), input=patch)
```

with one `inner._must("sh", ["-c", _VERIFY_CHECKOUT, "sh", self.synced, tree], cwd=str(self.tree), input=patch)` under the same `if patch:`. `_VERIFY_CHECKOUT` is a module-level string constant holding the script above (a constant, not state). Nothing else in `open_verify` changes; `tree` still comes from `_judge_patch` and no new expression depends on `patch`. A non-zero exit raises `SandboxFailed` through `_must`, the existing `except` closes the box, and the binding is left as it was.

**`commits_out`**: signature `wrote=None`; `wrote = permissions.wrote_map(wrote)` right after the import; pass `wrote=wrote` to `judge_commits`. In the comparison with the verified tree, replace the `diff-tree --name-only` call and the `n not in wrote` filter with:

```python
left = [p for p, status, mode, new in permissions._diff_paths(self.tree, self._verified, got)
        if not permissions.wrote_blob(wrote, p, status, mode, new)]
```

`_diff_paths` runs `diff-tree -r --raw -z --no-renames` and takes two tree ids as it takes two commits. It raises `GitFailed`, a `PermissionBreach`, which the existing `except` turns into a refusal.

**`commit`**: `wrote=None`, passed through.

**New `Remote.verified_text(self, path) -> str | None`**, next to `end_verified`:

```python
def verified_text(self, path) -> str | None:
    """`path`'s text in the tree of the last green verify round, read from the host's
    object store. None when no round has gone green or the binding has ended. The
    build VM's disk is not read. Raises PermissionBreach (GitFailed) if the tree has
    no such file."""
    from . import permissions
    if not self._verified:
        return None
    return permissions._host_read(self.tree, ["cat-file", "blob", f"{self._verified}:{path}"]).decode("utf-8")
```

`path` is the repo-relative path the chain already passes in `wrote`. Use `_host_read` (bytes, not stripped), never `_host_git`, which strips the last newline. `VerifyBox` gets no such method.

**Docstrings**: the module docstring's verify bullet (near lines 25 to 35), `_judge_patch`, `open_verify`, `commits_out` and `commit`. Each must say what applies to it: the verify VM holds a checkout of the judged tree, proved by its tree id; a `wrote` path is excepted only with its blob.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`

- New `blob_id(text: str) -> str`: the id `git hash-object --stdin` gives `text.encode()`, run on the host with `subprocess.run` in the current directory (never through `sandbox.current()`, which is the VM), with the same no-program flags the module's other host calls use if it has any, `check=True`, bytes in. No `-w`, no `--path`: nothing is written and no filter runs.
- `commit_all`: `wrote: dict[str, str] | None = None`. Before `git add`, `wrote = permissions.wrote_map(wrote)` (import inside the function, as the function already does for `permissions`). Pass it to `check_local_commit` and `box.commit`. Docstring: `wrote` maps each path the chain wrote into this commit to the blob it wrote; the path passes only with that blob.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/tickets.py`

`mark_done(ticket) -> dict[str, str]`:

```python
tree = sandbox.current()
verified = getattr(tree, "verified_text", None)
text = verified(ticket.path) if verified else None
if text is None:
    text = tree.read_text(ticket.path)
... the Status edit, unchanged ...
tree.write_text(ticket.path, text)
return {ticket.path: git_helper.blob_id(text)}
```

Import `git_helper` beside `sandbox` (check it makes no import cycle; if it does, import inside the function). Docstring: under a VM the text starts from the ticket in the last green tree on the host, so what the build VM's disk holds is never read; under `sandbox: none` the local file is read, since no tree is bound there; the return value is what `commit_all` takes as `wrote`. Update the module docstring's sentence on `mark_done`.

### 5. `.claude/skills/sssf/templates/adws/adw_tickets.py`

- `commit(ph, subject, body="", step="", wrote=None)`.
- In `tNN_commit`: `wrote = tickets.mark_done(ticket)`, then `commit(ph, …, wrote=wrote)`.
- If the module docstring describes the done mark, add one clause: the commit is held to the blob the chain wrote.

No other chain passes `wrote`. Before editing, search `templates/adws/` for `wrote=` and confirm.

### 6. Tests

All under `.claude/skills/sssf/tests/`. The config is the shipped `templates/sssf.config.yaml`, which `rig` loads. A "left process" is the test writing in `rig.vm_tree` outside `_as_builder`.

**`.claude/skills/sssf/tests/test_verify_commit_tree.py`**

Call sites to move to a map (they assert the old API):

- `test_a_path_in_wrote_may_differ_from_the_verified_tree_but_a_second_one_may_not`: pass `wrote={"feat.txt": git_helper.blob_id("v2\n")}`. Add a third case: the same map with `feat.txt` holding `"v3\n"` at the commit is refused and the host `HEAD` doesn't move; and a fourth: `feat.txt` deleted, refused.
- `_chain_tickets`: `wrote={mark: git_helper.blob_id("resolved\n")}`.

New tests. A helper `_clean_checkout(rig, tmp_path, tree) -> Path` checks `tree` out of the host's object store into an empty folder from an empty index (for example `git --git-dir=<rig.host_tree>/.git --work-tree=<folder> read-tree -u --reset <tree>` with `GIT_INDEX_FILE` set to a new temp path). The tree id is `rig._judge_patch(rig._patch_out())`, taken before the round.

1. `test_verify_checks_a_checkout_under_the_patched_gitattributes`, parametrized:
   - `ident`: `_setup`; through `_as_builder`, write `.gitattributes` with `app.py ident\n` and append `# $Id$\n` to `app.py`. The check is `grep -qx '# \$Id\$' app.py`.
   - `crlf`: through `_as_builder`, add `run.sh` holding `echo hi\n` and `.gitattributes` with `*.sh eol=crlf\n`. The check is `test "$(wc -c < run.sh)" = 8` (LF bytes; a CRLF checkout has 9).
   - `crlf_untouched`: `run.sh` is committed and synced first, and the patch adds only `.gitattributes`. Same check.
   Each asserts: the check's exit status in `_clean_checkout` is non-zero; `_verify(rig, check).passed is False`; and with the check also given as `base=_block(check, seen)`, the same. Red on the baseline (the check passes in the verify VM), green after.
2. `test_a_verify_vm_whose_tree_is_not_the_hosts_fails_the_round`: `_pending`; patch `rig._judge_patch` to return the last sync's tree id; `_verify` raises `SandboxFailed`, a `ran` list in `base` stays empty, the verify stand-in is destroyed, and the host `HEAD` is the sync.
3. `test_the_verify_vm_keeps_head_and_index_at_the_last_sync`: after a green round with a pending change, a `base` that runs `git rev-parse HEAD` and `git status --porcelain` in the verify VM sees the sync and the change listed as uncommitted, as on the baseline.
4. Add `_attrs` (writes `.gitattributes` with `*.txt text\n`, which changes no file's bytes) to the `change` lists of `test_a_real_run_still_verifies_and_lands_with_the_verified_tree` and `test_each_chains_ordinary_run_lands_every_commit`.
5. `test_a_ticket_rewritten_before_the_mark_lands_as_the_green_tree_has_it`: `_setup`; through `_as_builder`, add `issues/01-a.md` with a title line, `**Status:** ready-for-agent` and an `Accept: the page loads` line. `_verify(rig, CHECK)` green. A left process rewrites the `Accept:` line in `rig.vm_tree`. Then, as `tNN_commit` does, `wrote = tickets.mark_done(Ticket(number="01", slug="01-a", path="issues/01-a.md", title="A"))` and `git_helper.commit_all("t01", run=rig.run_, wrote=wrote)`. The host `HEAD`'s ticket equals the green text with only the Status line changed to `resolved`.
6. `test_a_ticket_rewritten_after_the_mark_is_refused`: the same, with the rewrite after `mark_done` and before `commit_all`. `PermissionBreach`, the host `HEAD` is the sync, `sandbox-refused.bundle` exists. A second parameter replaces the ticket with a symlink after the mark: refused too.

   To see the baseline's red for the right reason in 5 and 6, first run them on the unchanged code with `wrote = tickets.mark_done(t) or [t.path]`: each must fail because the rewrite landed. Then drop the `or [t.path]`. Say so in the build report.
7. `test_wrote_as_a_list_is_a_type_error_and_stages_nothing`: `commit_all("x", run=rig.run_, wrote=["feat.txt"])` raises `TypeError` and the VM's index holds no staged change.

**`.claude/skills/sssf/tests/test_verify_fresh_vm.py`**: no change planned. Run it; every test must pass with the new script.

**`.claude/skills/sssf/tests/test_sandbox_remote.py`**: line 1749 passes `wrote=["justfile"]`. Pass a map with the blob of the text that test writes to `justfile`. Add beside it: the same commit with other content at `justfile` is refused.

**`.claude/skills/sssf/tests/test_commit_check.py`**: line 93 passes `wrote=[".scratch/t/1.md"]`. Pass a map with that file's blob in `tip`. Add `test_a_wrote_path_with_another_blob_is_judged_like_any_other_path`: with the map naming another blob, `judge` returns the path as refused when no agent may write it, and `[]` when an agent's `writes` covers it. Add the same through `check_local_commit` (`sandbox: none`): a local commit whose `wrote` path holds another blob is refused, the branch goes back to `before`, and the file is kept; with the right blob it passes.

**`.claude/skills/sssf/tests/test_secret_commits.py`** (protected; the `guard_builder`'s `writes` names it): line 117 passes `wrote={"src/.env.local"}`, a set. Pass a map from that path to its blob in the commit. The assert stays: a secret at a `wrote` path is still refused.

**`.claude/skills/sssf/tests/test_tickets.py`**: add `test_mark_done_returns_the_blob_it_wrote`: the returned map has one key, the ticket's path, and its value equals `git hash-object <file>` after the call. The two existing `mark_done` tests stay.

**`.claude/skills/sssf/tests/test_adw_tickets.py`**: `VMTree` has no `verified_text`, so `mark_done` reads its disk; no change needed for that. Add to `VMTree` a `verified_text(self, path)` that records the call and returns the file's text from `git show HEAD:<path>`, and assert in `test_a_sandbox_run_builds_on_its_own_branch_and_marks_tickets_done_in_the_vm` that it was asked once per ticket. That test is `host_only`, so it runs in SSSF's verify, not in the builder's run.

Search every test file for `wrote=` once more after the edits; none may pass a list or a set, except test 7.

### 7. Docs

- `.claude/skills/sssf/references/config.md`, "Where verify runs under a VM": replace "clones the branch at the last sync, applies the patch" with what happens now: it clones the branch at the last sync, applies the patch to the index, compares the tree id with the one the host built (a mismatch fails the round before any check runs), and checks every file out from that index, so the checks see a clean checkout of the verified tree under its own `.gitattributes`; `HEAD` and the index are then the last sync again. Replace "(except the paths the chain wrote itself, `wrote`)" with: except a path the chain wrote itself, which passes only with the blob the chain hashed on the host (`wrote` maps the path to that blob); under a VM the ticket's done mark starts from the ticket in the last green tree, not from the build VM's disk. In "What changes for a repo", reword the `wrote` bullet: a chain that writes a file between verify and the commit must pass `wrote={path: git_helper.blob_id(text)}`; a list of paths is a `TypeError`. Add the ticket limit from the Bar (bytes git's clean conversion changes).
- `docs/open.md`: the two P2 rows (lines 149 and 150 at the baseline). Follow the file's own convention for a row a run has closed (read its header and an already closed row). Each row must say what this run closes, and link `specs/af37b9f1_verify-checkout-wrote-blob.md` beside the old spec. Add one P3 row for what stays open from Out of scope below (the `sandbox: none` read, and a `wrote` ticket with CRLF or `ident`), if the file has no row for them. Do not touch the P3 rows on O2, O4, H2, H3 or the `/tmp/conftest.py` row.
- `docs/designs/remote-sandboxes.md`: where it describes `open_verify` applying the patch and where it names `wrote`, the same facts in two or three sentences.
- Docstrings named in steps 1 to 5.
- The repo keeps no CHANGELOG. `README.md` and `SKILL.md` don't describe this.
- For the documenter: any `app_docs/` write-up of runs dafc21f0, 3d9f277f or 1befc519 that says the verify VM applies the patch to its working files, or that a `wrote` path is not compared, describes old behaviour. Note it in the new write-up; do not edit the old ones.

## Out of scope

- `docs/open.md`'s P3 rows on a left process's commits at `close()` (O2, O4) and on the verify VM's error paths (H2, H3). A tree mismatch in the verify VM is a new failed round and falls under H2/H3's handling as it stands.
- `sandbox: none`: a rewrite of the ticket before `mark_done` reads it lands. Its `/tmp/conftest.py` row.
- A ticket whose bytes `git add` converts (CRLF under `text`, an expanded `$Id$`): its commit is refused, not landed.
- A repo with a populated submodule: `git rm -r` and `checkout-index` in the fresh clone are not tested with one. The fresh clone has none initialised, as on the baseline.
- A filter driver named in `.gitattributes` (`filter=`): the fresh VM has no such driver configured, so the checkout writes the blob as it is, as a clean clone without that config does.
- The documenter's commit, and a run with no verify round: as in `specs/1befc519_verified-tree-binding.md`.
- The reviewers read the build VM's tree.
- The text guard. Its patches are frozen.

## Verification

Before the code changes, write the step 6 tests and run the files. Report which are red on the baseline: test 1 (all three cases, the check passing in the verify VM), tests 5 and 6 (with the `or [t.path]` line, the rewrite landing), and the new `test_commit_check.py` cases. Tests 2, 3 and 7 fail on the baseline for the missing feature; say so.

After, from `.claude/skills/sssf`, run only the touched files:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_verify_commit_tree.py tests/test_verify_fresh_vm.py tests/test_sandbox_remote.py tests/test_commit_check.py tests/test_secret_commits.py tests/test_tickets.py tests/test_adw_tickets.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

All must pass, judged by the exit status. Add any other test file the `wrote=` search made you change. The whole suite is left to SSSF's verify, which runs `tests/` and so runs the new tests; the plan adds no extra check.

Read the diff against `70a4073` and confirm:

- it touches none of `templates/sssf.config.yaml`, any prompt under `templates/`, `rules.py`, `sandbox_openshell.py`, `agentsh_sssf.yaml`, or anything under `adws/`, `specs/` or `app_docs/`;
- `open_verify` has no `git apply` on working files left, and no expression that depends on `patch` other than the `if patch:` around the script and `patch_bytes` in the event;
- `path in wrote` and `n not in wrote` appear nowhere under `templates/adws/`;
- `self._verified` is assigned only where it was (`__init__`, `open_verify`, `commits_out`, `end_verified`, `VerifyBox.mark_green`);
- `mark_done` under a `Remote` with a green tree makes no `read_text` call (test 5 proves it by the bytes that land).

No test here starts a real VM. The stand-ins share the host's git, so the real-VM proof is the engineer's.

## For the engineer

- The hub's own `adws/` is a separate copy of the templates and is protected, so this run does not touch it. After the merge, update the hub's SSSF so `adws/adw_modules/sandbox_remote.py`, `adws/adw_modules/permissions.py`, `adws/adw_modules/git_helper.py`, `adws/adw_modules/tickets.py` and `adws/adw_tickets.py` get the change. Until then the hub's chains run the old rule. `adws/adw_guard_change.py` passes no `wrote` and needs no edit.
- On a real VM (`--sandbox openshell`), rerun the reviewer's two probes: the `app.py ident` plant with a check on the `$Id$` line (verify must go red), and a tickets run where a left process rewrites a ticket's "Accept:" line before the mark (the commit must hold the green text) and after it (the commit must be refused). Agents under agentsh can't start a VM.
- Check `git checkout-index` and `git rm --ignore-unmatch` on the VM image's git version; both are old commands, but the image's git is not the host's.
