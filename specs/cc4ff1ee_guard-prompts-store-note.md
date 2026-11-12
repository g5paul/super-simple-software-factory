# Keep test data and secret probes clear of the rules, and make the store note name the audit files

Next items 20 and 21 in `docs/designs/agentsh-local-sandbox.md`. Brief: `specs/tasks/guard-agent-prompts-and-store-note.md`.

## Summary

Four prompt files get a rewritten file-tool line (create **and edit** with file tools; put inline text that names a forbidden command or a secret file in a file, then run that file) and a new decoy line (probe a decoy in a scratch repo you made, never the repo's own secret files). `store_notes()` gives both sizes, and its advice is for whichever part is bigger. `config.md` describes the new note and lists the two root-path link limits. The guard chain's `build_brief()` copy command and its test are in protected files, so they're under **For the engineer**.

## Baseline

`main` at `d4646b7` ("Add the brief for the agent-prompt and store-note guard run"), which has run c0f9d9d8 merged as 6c829e6. Every finding is judged against `d4646b7`.

## Threat model

- An agent runs as the engineer's own user, under `local_guard: agentsh` on the hub. It can read and write the workspace, run any command the text rules and agentsh's policy let through, and write files with its file tools, which the text rules never see.
- The text rules (`rules.py`, `sssf_refuse.py`, `sssf_rules.ts`) search a shell command's text. They can't tell a command that *runs* `git push` or opens `.env` from one that only carries those words as data. That's why the 11 refusals happened, and this change doesn't alter it. It only changes the prompts so that agents stop putting such data in commands.
- A prompt line is a request, not a guard. Nothing in this change enforces anything new. A prompt line that grants something (for example "run any file you wrote") would weaken the baseline, so the new lines keep "The rules apply to what you run, however you run it" and grant nothing.
- `store_notes()` reads a world-readable status file that only root's prune timer writes. Its output is advice printed and traced at the agent's start. An agent can't write the status file, and the note never refuses or stops.
- `/var/lib`, `/var/lib/agentsh` and `/var/lib/agentsh/events.db` belong to root. No agent can make a link there. This run documents the two link limits and doesn't fix them.

## Bar

Must:
- Change the file-tool line in exactly four prompts (listed in Step 1) and add one decoy line to the same four, and touch no other prompt line.
- Make `store_notes()` give both sizes it has, and advise on the rotated `audit.jsonl.<N>` files when `audit_bytes` is the bigger part. When the store is the bigger part, or the two are equal, the advice is the same as at baseline.
- List the two limits in `config.md` under "Running local agents under agentsh", and describe the new note there.

Must never:
- Change any rule, regex, pattern, refusal or stop: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `secret_files.py`, `agentsh_sssf.yaml`, the policy rendering, `check_machine`'s refusals, and `agentsh_prune.py` all stay byte-for-byte as at baseline.
- Make the store note refuse, raise or stop. It still returns `[]` on any exception, still reads only `PRUNE_STATUS`, and still prints and traces through the same path. The 15 GB threshold (`STORE_WARN_BYTES`) and the "fire when the sum ≥ 15 GB" condition don't change.
- Add a prompt line that grants an agent anything the baseline prompts deny.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't.

## Out of scope

These are old holes, and a reviewer should file them that way:
- The text rules' false positives on a command's data, including `rsync --exclude .env`. The text guard is frozen. That case belongs in `docs/designs/agentsh/corpus.sh` and is the engineer's job.
- Any link above the audit folder (`/var/lib`) and a link at the store path (`events.db`). Both are documented here, not fixed.
- Every prompt other than the four in Step 1, including the hub's `adws/adw_data/prompt_engineering/reviewer/system.md`, which keeps the old line, so after this change it differs from the shipped reviewer template.
- pi's policy.
- `config.md`'s sentence that the audit cap "holds it to 1.2 GB whatever the rate" doesn't match the 3.73 GB measured on 2026-11-11 (files left over from agentsh's old defaults). Leave that sentence as it is. It's noted for the engineer below.

## Files to change (builder)

- `.claude/skills/sssf/templates/prompt_engineering/builder/system.md`
- `.claude/skills/sssf/templates/prompt_engineering/reviewer/system.md`
- `adws/adw_data/prompt_engineering/builder/system.md` (the hub's copy, identical to the shipped builder today)
- `adws/adw_data/prompt_engineering/bypass_reviewer/system.md`
- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` (`store_notes()` only)
- `.claude/skills/sssf/tests/test_agentsh_guard.py`
- `.claude/skills/sssf/references/config.md`

None of these is in `protected_files`. Don't touch `adws/adw_guard_change.py`, `adws/tests/`, `adws/adw_modules/`, `specs/` or `app_docs/`.

## Step 1: The file-tool line (item 1)

### Shipped builder, shipped reviewer, hub builder

In `.claude/skills/sssf/templates/prompt_engineering/builder/system.md` (line 16), `.claude/skills/sssf/templates/prompt_engineering/reviewer/system.md` (line 19) and `adws/adw_data/prompt_engineering/builder/system.md` (line 16), replace this line:

```
- Write files with your file tool, never through the shell with a heredoc or `echo`. The repo's rules search every shell command, text included, so a file written with `cat <<EOF` that names a forbidden command is refused. The rules apply to what you run, however you run it.
```

with:

```
- Create and edit files with your file tools, never through the shell: no heredoc, `echo`, `cat >`, `sed -i` or inline Python such as `python3 - <<'EOF'` or `python -c`. The repo's rules search every shell command, text included, so a command that carries a forbidden command or a secret file's name only as data, such as a test case, a comment or a search pattern, is refused like one that runs it. When a script or check needs such text, write it to a file with your file tool and run that file, and search file contents with your grep tool. The rules apply to what you run, however you run it.
```

The three files must carry the same line, character for character. The hub builder stays identical to the shipped builder (`diff` of the two files is empty after the change).

### Hub bypass reviewer

In `adws/adw_data/prompt_engineering/bypass_reviewer/system.md`, under `## Rules` (line 47), replace:

```
- Write files with your file tool, never through the shell with a heredoc or `echo`. The repo's rules search every shell command, text included.
```

with:

```
- Create and edit files with your file tools, never through the shell: no heredoc, `echo`, `cat >`, `sed -i` or inline Python such as `python3 - <<'PY'` or `python -c`. The repo's rules search every shell command, text included, so a command that carries a forbidden command or a secret file's name only as data, a candidate, a comment or a search pattern among them, is refused like one that runs it. When a script or check needs such text, write it to a file under your scratch directory with your file tool and run that file, and search file contents with your grep tool. The rules apply to what you run, however you run it.
```

The baseline line lacks "The rules apply to what you run, however you run it." The new line adds it. That sentence only restricts the agent, so the line matches the other three. This line doesn't change the bypass reviewer's existing rule that a candidate is only ever a string handed to a check, never run as a real shell command.

## Step 2: The decoy line (item 2)

Add one line right after the new file-tool line in each of the four files.

Shipped builder, shipped reviewer, hub builder (same text in all three):

```
- To test a rule on secret files, probe a decoy: make a scratch repo of your own in a temp folder outside this repo, put a decoy secret file there, and point the probe, written to a file as above, at that decoy. This repo's own secret files stay closed to every command, a probe included. A refused or denied probe is an answer, never a reason to try the real file another way.
```

Hub bypass reviewer:

```
- To test a secret-file rule with a real action, make a decoy secret file in a scratch repo you create under your scratch directory, and probe that decoy. This repo's own secret files stay closed to every command, a probe included. A refused or denied probe is an answer, never a reason to try the real file another way.
```

No other line in any prompt changes.

## Step 3: `store_notes()` names the bigger part (item 4)

In `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`, change only the size block at the end of `store_notes()` (lines ~531–540). Keep the outer `try/except Exception: return []`, the stale-prune note, the missing-status note, `PRUNE_STATUS`, `STORE_WARN_BYTES` and the condition `sizes and total >= STORE_WARN_BYTES` as they are.

Shape:

```python
sizes = {key: data.get(key) for key in ("store_bytes", "audit_bytes")}
sizes = {k: v for k, v in sizes.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
total = sum(sizes.values())
if sizes and total >= STORE_WARN_BYTES:
    parts = []
    if "store_bytes" in sizes:
        parts.append(f"the event store is {sizes['store_bytes'] / 1e9:.1f} GB")
    if "audit_bytes" in sizes:
        parts.append(f"the audit log is {sizes['audit_bytes'] / 1e9:.1f} GB")
    text = (f"agentsh's event store and audit log are {total / 1e9:.1f} GB together, at least "
            f"{STORE_WARN_BYTES / 1e9:.0f} GB ({' and '.join(parts)}); ")
    if sizes.get("audit_bytes", 0) > sizes.get("store_bytes", 0):
        text += ("the audit log is the bigger part, in its rotated files /var/lib/agentsh/audit.jsonl.<N>. "
                 "A prune deletes each one once it is more than 2 hours old. To free the space sooner, with no "
                 "run going, run `sudo find /var/lib/agentsh -maxdepth 1 -type f -name 'audit.jsonl.[0-9]*' "
                 "-delete`, which leaves the live audit.jsonl")
    else:
        text += ("with no run going, stop the service, move /var/lib/agentsh/events.db and its -wal and -shm "
                 "files aside, start it, and delete the moved files once a run works")
    notes.append(text + " (see \"Running local agents under agentsh\" in "
                 ".claude/skills/sssf/references/config.md)")
```

Rules:
- The sum, the threshold and which status values count (numbers, not bools) stay exactly as at baseline, so the note fires on the same inputs.
- Audit advice only when `audit_bytes` is present and strictly bigger than `store_bytes` (or the store is missing). A tie, or the store alone, gives the baseline's store advice.
- The note stays one line with no `\n`.
- The `find` command doesn't follow a link at `/var/lib/agentsh` (find's default `-P`), and `-type f` skips links in the folder. The note is text, and SSSF runs nothing.
- Update the docstring to say the note gives both sizes and advises on the bigger part.

## Step 4: Tests (`.claude/skills/sssf/tests/test_agentsh_guard.py`)

Use the existing `machine` fixture (it points `PRUNE_STATUS` at a temp file) and `_write_status`. No test reads or writes `/var/lib/agentsh` or the repo's real secret file.

1. Update `test_check_machine_notes_the_store_and_audit_log_together` (line ~2742). Its `(None, 15e9, 1)` case now gets audit advice, so instead of always asserting `"events.db"`, assert `"15 GB"` in every note, `"events.db"` when the store is present and at least as big as the audit log, and `"audit.jsonl.<N>"` otherwise. The counts in that table don't change.
2. Add `test_store_note_names_the_bigger_part`:
   - store `10e9`, audit `6e9`: one note with `"10.0 GB"`, `"6.0 GB"`, `"16.0 GB"`, `"move /var/lib/agentsh/events.db"`, and no `"audit.jsonl.<N>"` or `"sudo find"`.
   - store `4e9`, audit `12e9`: one note with `"4.0 GB"`, `"12.0 GB"`, `"audit.jsonl.<N>"`, `"2 hours"`, `"-maxdepth 1 -type f -name 'audit.jsonl.[0-9]*' -delete"`, `"no run going"`, and no `"move /var/lib/agentsh/events.db"`.
   - store `8e9`, audit `8e9` (tie): store advice.
   - Every note has no `\n` and has the `config.md` pointer.
3. Add `test_store_note_with_one_size`:
   - only `audit_bytes=16e9` (store key `None`): audit advice, `"the audit log is 16.0 GB"`, no `"the event store is"`.
   - only `store_bytes=16e9`: store advice, `"the event store is 16.0 GB"`, no `"the audit log is"`.
   - `audit_bytes=True` with `store_bytes=16e9`: the bool doesn't count, so store advice and no audit size.
4. `test_check_machine_notes_a_big_store` and `test_check_machine_reads_an_old_status_as_the_store_alone` must still pass unchanged.

Write and edit the test file with the Edit tool, never with a heredoc or inline Python.

## Step 5: `config.md` (items 4 and 5)

`.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh, `local_guard`":

1. Line ~487, after "or the store and audit log together reach 15 GB.", add: "That note gives each size it has. When the audit log is the bigger part, its advice is for the rotated `audit.jsonl.<N>` files. Otherwise it's to move `events.db` aside."
2. In the "**The event store.**" paragraph (line ~502):
   - Change "It opens the audit folder without following a link." to "It opens the audit folder without following a link at the folder itself (see the limits below for a link above it)."
   - After the sentence ending "and delete them once a run works.", add: "When the audit log is the bigger part, the space is in the rotated files. A prune deletes each one once it is more than 2 hours old. To free it sooner, with no run going, run `sudo find /var/lib/agentsh -maxdepth 1 -type f -name 'audit.jsonl.[0-9]*' -delete`, which leaves the live `audit.jsonl`."
   - Change nothing else in that paragraph (the 1.2 GB sentence stays; see Out of scope).
3. At the end of the "More known limits, each with what still applies:" list (after the line ~561 bullet), add two bullets:
   - "The prune opens the audit folder with `O_NOFOLLOW`, which checks only the path's last part. A link above it, such as `/var/lib`, is still followed, and the prune lists and deletes rotated audit files in the folder it leads to. The paths are root's, and no agent can make the link."
   - "The store path, `/var/lib/agentsh/events.db`, follows a link: the prune opens it with `sqlite3.connect` and sizes it and its `-wal` file with `os.stat`. The path is root's, and no agent can make the link."

Before you write these bullets, check `.claude/skills/sssf/scripts/agentsh_prune.py` (`open_folder`, `store_bytes`, the `sqlite3.connect` call) so each bullet matches the code. Don't change the script.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: Step 5.
- The `store_notes()` docstring: Step 3.
- There's no CHANGELOG, and README usage doesn't change.
- `docs/designs/agentsh-local-sandbox.md` Next items 20 and 21: the hub session records runs there after merge. The builder doesn't edit it.
- For the documenter: any `app_docs/` write-up that says the 15 GB note only advises moving `events.db` now describes old behaviour. Note it, and don't edit the old write-up.

## For the engineer

These are in protected paths (`adws/adw_*.py`, `adws/tests/`). The builder must not touch them.

### Item 3: the guard chain's build brief gives the copy command

The hub ignores `.env` at `.gitignore` line 112 (`.env`, with `.env.*` on line 113 and `!.env.example`/`!.env.sample` re-included). The planner checked this, so the git-listed copy leaves it out.

In `adws/adw_guard_change.py`, add a constant above `build_brief()`, and a paragraph to the brief:

```python
COPY_COMMAND = ("git ls-files -z --cached --others --exclude-standard | "
                "rsync -a --no-links --from0 --files-from=- --ignore-missing-args ./ <copy>/")
```

Append to `build_brief()`'s text:

```
To run the suite from a copy outside the repo, make an empty folder outside it, such as one
`mktemp -d` makes, and from the repo root copy the files git lists into it, with `<copy>` as
that folder:

    {COPY_COMMAND}

`.gitignore` keeps `.env` out of the list, so the command never names it. The copy holds the
tracked files as they are in the working tree and the untracked files git doesn't ignore, and
leaves out links and `.git`. Never name a secret file in a command, even to leave it out.
```

Notes:
- `--ignore-missing-args` handles a tracked file deleted from the working tree.
- `--no-links` skips tracked links, such as `.githooks/pre-push`, because agentsh denies making a relative link, and following one could copy a file outside the list. rsync prints "skipping non-regular file" and exits 0.
- The planner ran the command in a temp repo whose ignored file was listed in `.gitignore`: exit 0, the ignored file and the link left out, and the modified file copied with its new content.

Test in `adws/tests/test_guard_change.py` (imports `adw_guard_change as gc`, and `subprocess` and `os` are already imported; add `import shlex`):

```python
def test_the_build_brief_copy_leaves_the_ignored_secret_out(tmp_path):
    assert gc.COPY_COMMAND in gc.build_brief("specs/x.md")
    assert ".env" not in gc.COPY_COMMAND
    repo, copy = tmp_path / "repo", tmp_path / "copy"
    repo.mkdir(); copy.mkdir()
    git = lambda *a: subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                    cwd=repo, check=True, capture_output=True)
    git("init", "-q")
    (repo / ".gitignore").write_text(".env\n")
    (repo / "tracked.txt").write_text("one")
    (repo / "modified.txt").write_text("old")
    (repo / "gone.txt").write_text("x")
    git("add", "-A"); git("commit", "-qm", "init")
    (repo / "modified.txt").write_text("new")
    (repo / "gone.txt").unlink()
    (repo / "untracked.txt").write_text("u")
    (repo / ".env").write_text("SECRET=decoy")
    subprocess.run(["bash", "-c", gc.COPY_COMMAND.replace("<copy>", shlex.quote(str(copy)))],
                   cwd=repo, check=True, capture_output=True)
    assert sorted(p.name for p in copy.rglob("*")) == [".gitignore", "modified.txt", "tracked.txt", "untracked.txt"]
    assert (copy / "modified.txt").read_text() == "new"
```

It needs `git` and `rsync` (both on the hub). `just chain-test` (the `chain_test` quality check) runs it.

### Also for the engineer

- `config.md`'s "the cap holds it to 1.2 GB whatever the rate" doesn't match the measured 3.73 GB (the design doc puts it down to leftover `.6` to `.10` files from agentsh's old defaults). Decide whether to reword it. This run leaves it alone.
- Add `rsync --exclude .env` as a case in `docs/designs/agentsh/corpus.sh`, per Next item 20.
- After merge, `stamp.json` records for the two shipped prompt templates change. Update the hub with the usual skill update.

## Verification

Run the hub suite from a copy outside the repo, as earlier runs did. Copy with the git-listed command above, with `<copy>` as a fresh `mktemp -d` folder, and never name the secret file in the command:

```
git ls-files -z --cached --others --exclude-standard | rsync -a --no-links --from0 --files-from=- --ignore-missing-args ./ <copy>/
```

Then, in the copy:

1. `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider` exits 0, with the store note tests above among them.
2. `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests -q -p no:cacheprovider` exits 0, unchanged.
3. In the repo: `diff .claude/skills/sssf/templates/prompt_engineering/builder/system.md adws/adw_data/prompt_engineering/builder/system.md` exits 0.
4. `git diff --stat d4646b7` lists only the seven files in "Files to change", and each prompt diff is exactly the replaced file-tool line plus the one added decoy line.
5. `git diff d4646b7 -- .claude/skills/sssf/templates/adws/adw_modules/` touches only `store_notes()` in `agentsh_guard.py`.
6. The plan check: each of the four prompts contains "decoy" and "however you run it".
