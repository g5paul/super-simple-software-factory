# Keep 2 hours of agentsh's events, note at 15 GB, and refuse a linked audit folder

Task: `specs/tasks/guard-agentsh-retention-2h.md` (run H).

## Summary

Three small changes to the SSSF skill's agentsh prune and guard, plus the docs that state their values:

1. `KEEP_SECONDS` in `.claude/skills/sssf/scripts/agentsh_prune.py` goes from 6 hours to 2 hours. It sets the age limit for both the event rows and the rotated audit files.
2. `STORE_WARN_BYTES` in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` goes from 10 GB to 15 GB. The note fires at 15 GB or more of store plus audit log (`>=`), and its text takes the number from the constant.
3. The prune's audit step opens the audit folder with `O_DIRECTORY | O_NOFOLLOW` and does its listing, `lstat` and `unlink` through that one fd (`dir_fd`). If the folder is a link, missing, or not a directory, the open raises `OSError`. That deletes nothing, and the existing `except OSError` records the error and makes the run exit 1, as for any other audit error.

Every edit goes under `.claude/skills/sssf/`. The hub's own `adws/` copies are protected; see "For the engineer".

## Baseline

`main` at `3ee1333` ("Record run 87b8ab19 and write run H's spec"). It includes a182d1f, the merge of run 87b8ab19. Every finding is judged against `3ee1333`. On the baseline:

- `KEEP_SECONDS = 6 * 3600`. A due prune deletes event rows with `ts_unix_ns` older than that, and rotated `audit.jsonl.<N>` files whose `mtime` is older than that.
- `STORE_WARN_BYTES = 10 * 10**9`. `store_notes` adds a note when `store_bytes + audit_bytes > 10e9`, and the text says "over 10 GB".
- `rotated(audit)` calls `os.listdir(os.path.dirname(audit))` and then `os.lstat(path)` on each full path, and `prune_audit` calls `os.unlink(path)` on full paths. If `/var/lib/agentsh` is a symlink, all three follow it, so old `audit.jsonl.<N>` files in the link's target get deleted. Because the steps use path names, a folder swapped between the listing and the unlink is also followed.

## Threat model

- Agents run as the engineer's own user, under agentsh when `local_guard: agentsh`. They can't write to `/var/lib/agentsh`, `/var/lib/sssf-agentsh-prune`, `/usr/local/lib/sssf-agentsh/` or `/etc/systemd`, which are root's. They can't create, replace or relink `/var/lib/agentsh`. The prune runs as root from a systemd unit and runs no agent code.
- What an agent *can* do at this level: make many events, which grows the store and the audit log; keep a session open, so the prune skips; and read the status file, which anyone can read. This guard can't stop any of those. The 15 GB note and the stale-prune note report them, and refuse nothing.
- The linked-folder fix is defence in depth against a root-level misconfiguration (an operator or a package making `/var/lib/agentsh` a link). No agent can trigger it. It must never make the prune delete *more*.
- `O_NOFOLLOW` checks only the last part of the path (`agentsh`). A link at `/var` or `/var/lib` is still followed. Those are root's system folders, and this change leaves them out of scope.

## Bar

The change must:

- Delete event rows older than `2 * 3600` seconds: a row `2 * 3600 + 1` s old is deleted and one `2 * 3600 - 1` s old is kept. Rotated audit files use the same age.
- Note at 15 GB or more of `store_bytes + audit_bytes`, with an old status file that has no `audit_bytes` read as the store alone. A total of 15e9 notes and 15e9 − 1 doesn't.
- Delete nothing in the audit step when the audit folder is a symlink, missing, or not a directory. Every file in a link's target stays. The run records `error` and exits 1, like any other audit error.
- List, `lstat` and `unlink` through one fd on the folder, so a folder swapped after the open can't move the deletes somewhere else.

It must never, against the baseline:

- Keep an agent file, run agent code, or delete or overwrite an engineer file. The prune deletes only regular files named `audit.jsonl.<ASCII digits>` in the real audit folder, and never the live `audit.jsonl`.
- Change any of these: the session skip and its checks before each batch, before `ANALYZE` and before the audit step; `BATCH`, `PAUSE` and `LOCK_WAIT`; the 50-minute due check (`PRUNE_EVERY`, `PRUNE_SLACK`); `ANALYZE_SECONDS`; the status keys and their meanings; the exit codes; `fix_owner`; the rotation keys in `agentsh_sssf.yaml` (`max_size_mb: 200`, `max_backups: 5`) or any other non-comment yaml key; `PRUNE_STALE_SECONDS`; `check_machine`'s refusals; pi's read loop; or any other refusal or stop.

One status value changes on purpose. `audit_bytes` is `null` when the audit folder is a link, as it already is when the folder is missing, because both now go through the same `O_NOFOLLOW` open. The key stays. The guard then counts the store alone, as it does for an old status file.

A known consequence, the same as the baseline's for any audit error such as a missing folder: while the folder is a link, `last_prune` doesn't advance. Every 10-minute check is then due and prunes the store again, and after 3 hours the stale-prune note names the recorded error. Leave that as it is.

## Out of scope

Everything run F's spec (`specs/tasks/guard-agentsh-retention.md`) puts out of scope, which stays an old hole:

- `VACUUM` or any shrinking of the store file. A store that already grew past 15 GB under the 6-hour setting keeps its file size, and keeps firing the note, until the manual reclaim in `config.md`.
- A prune while a session is listed. Chains that hold a session open at every check, or a session left open, still prune nothing.
- Keeping fewer allowed events through a fork patch.
- Compressing rotated audit files.
- Making `max_age_days` work.
- Closing the fork race in `stop_tree` or the partial-line hole.
- Decoding pi's pipe with `errors="replace"`.
- pi's policy.

Also out of scope here:

- Links at `/var` or `/var/lib`, where `O_NOFOLLOW` checks only the last part of the path.
- The store's own path (`DB`), which `sqlite3.connect` opens and which this change doesn't touch.
- The status folder.
- agentsh's own writes to a linked folder.
- Measuring the audit log's rate (see "For the engineer").

## Files to change

### `.claude/skills/sssf/scripts/agentsh_prune.py`

1. Module docstring, first line: "to the last 6 hours" becomes "to the last 2 hours".
2. `KEEP_SECONDS = 2 * 3600`.
3. Add a helper above `rotated`:

   ```python
   def open_folder(audit):
       """An fd on the audit log's folder. Raises OSError when the folder is a link, missing or not a directory,
       so nothing is listed or deleted through a link."""
       return os.open(os.path.dirname(audit), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
   ```

4. Change `rotated(audit)` to `rotated(audit, fd)`:
   - List with `os.listdir(fd)`.
   - `lstat` each matching name with `os.lstat(name, dir_fd=fd)`.
   - Return `(name, lstat)` pairs. Keep the name filter exactly as it is: `startswith(prefix)`, with the suffix `isascii()` and `isdigit()`.
   - Update the docstring to say it works on bare names in the folder the fd holds.
5. `audit_bytes(audit)`:
   - Open with `open_folder` inside the existing `try`, and close the fd in a `finally`.
   - Sum `rotated(audit, fd)`, then add the live log's size from `os.lstat(os.path.basename(audit), dir_fd=fd)` when it is a regular file. Keep the existing `FileNotFoundError` pass.
   - Any `OSError`, a linked folder included, still returns `None`.
   - Docstring: "or None when the folder is a link or can't be listed".
6. `prune_audit(audit, now)`:
   - `fd = open_folder(audit)`, then `try:` run over `rotated(audit, fd)` and call `os.unlink(name, dir_fd=fd)` for each file older than `KEEP_SECONDS`, keeping the `FileNotFoundError` pass, and `finally: os.close(fd)`.
   - Don't catch the `OSError` from `open_folder`. `_prune`'s existing `except (..., OSError)` records it as `"<Type>: <message>"` and sets `errored`. The message names the path, for example `OSError: [Errno 40] Too many levels of symbolic links: '/var/lib/agentsh'`.
   - Docstring: add "Deletes nothing when the folder is a link."
7. Leave the rest of `_prune` as it is.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

1. `STORE_WARN_BYTES = 15 * 10**9`.
2. In `store_notes`, change `total > STORE_WARN_BYTES` to `total >= STORE_WARN_BYTES`, so the note fires at 15 GB, as the task asks.
3. Change the text to `f"... are {total / 1e9:.1f} GB together, at least {STORE_WARN_BYTES / 1e9:.0f} GB; with no run going, ..."`. Keep the rest of the message word for word: the "move /var/lib/agentsh/events.db" advice and the config.md pointer.
4. Change nothing else in the module.

`render_config` dumps the yaml through `yaml.safe_load` and `yaml.safe_dump`, so a comment change in `agentsh_sssf.yaml` doesn't change the rendered config. `check_machine` won't report "differs" after the yaml edit below.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`

Change only the comment above `rotation:`. Keep its first three lines, about rotation by size. Replace the rest with about this:

```
  # 1.2 GB; the prune deletes rotated files older than 2 hours. The store grew
  # 1.4 GB an hour at the rollout and 3.3 GB an hour with chains back to back
  # (2026-11-11), so 2 hours plus up to an hour before the next prune levels off
  # at about 2.8 to 4.2 GB, and 6.6 to 9.9 GB at the peak. With the audit log's
  # 1.2 GB cap that stays under the 15 GB note. See config.md, "The event store".
```

No key or value changes.

### `.claude/skills/sssf/scripts/agentsh_install.py`

Line 147, the unit's `Description=`: "to 6 hours" becomes "to 2 hours". The unit text changes, so the next `agentsh_install.py` run shows that line in its diff, which is expected.

### `.claude/skills/sssf/references/config.md`

1. Line 487 (the notes paragraph): "or the store is over 10 GB" becomes "or the store and audit log together reach 15 GB".
2. Line 502 ("**The event store.**"). Make these changes and leave every other sentence as it is:
   - "deletes events older than 6 hours" becomes "deletes events older than 2 hours".
   - "last modified more than 6 hours ago" becomes "last modified more than 2 hours ago".
   - Directly after "it never deletes the live `audit.jsonl`, a link or any other name", add: "It opens the audit folder without following a link. If `/var/lib/agentsh` is a link, or missing, it deletes nothing, records the error in the status file, `audit_bytes` is empty, and the service exits 1."
   - Replace "so it levels off at about 6 hours of events, plus up to an hour before the next prune. At the hub's measured 1.4 GB an hour while chains run, that is about 8.4 to 9.8 GB." with: "so it levels off at about 2 hours of events, plus up to an hour before the next prune. The hub measured 1.4 GB an hour two hours after the rollout, and 3.3 GB an hour with chains running back to back on 2026-11-11 (4.58 GB at 14:50 and 5.95 GB at 15:29 after a 13:26 reset), so the store levels off at about 2.8 to 4.2 GB, and 6.6 to 9.9 GB at that peak. Nothing reads an event after its call ends; the longest call measured, a 31-minute review, fits well inside 2 hours."
   - Replace "Its rate hasn't been measured; at an estimated rate like the store's, the cap holds under an hour of events, and less busy hours keep 6." with: "Its rate hasn't been measured on the hub yet; the cap holds it to 1.2 GB whatever the rate, and the prune keeps at most 2 hours of rotated files."
   - Replace "The 10 GB note counts the store and the audit files together, so it can fire during a long busy stretch even when the prune is keeping up." with: "The 15 GB note counts the store and the audit files together. At the measured peak the two settle at about 11 GB, so the note means the prune isn't keeping up (a session held open, or a store that grew under the old 6-hour setting and hasn't been reclaimed: deleting rows doesn't shrink the file)."
   - Keep the existing "To reclaim space ..." sentence right after.

### `.claude/skills/sssf/tests/test_agentsh_guard.py`

Move the old values to the new ones, and add the link tests. Build any config from `TEMPLATE` (the shipped `agentsh_sssf.yaml`) as the file already does, never a test-only config. The tests never touch `/var/lib/agentsh`.

1. Rename `test_the_prune_keeps_the_last_six_hours_in_several_batches` to `..._last_two_hours_...`. Use the ages `2 * 3600 + 1` and `2 * 3600 - 1`, and expect `ages() == [2 * 3600 - 1, 0]`, still with `deleted == 8` and 3 DELETE statements.
2. In `_old_audit_files`:
   - Make `audit.jsonl.2` `2 * 3600 + 1` s old, so it is deleted.
   - Add `t("audit.jsonl.5", 2 * 3600 - 1)`, which must be kept. Add it to the expected list in `test_a_due_prune_deletes_only_old_rotated_audit_files`.
   - `audit.jsonl.1` at 3600 stays, and is kept.
3. `test_an_audit_unlink_error_is_recorded_and_exits_1`: the patched `os.unlink` now gets a bare name and `dir_fd`. Make it raise when `path == old.name and k.get("dir_fd") is not None`, and otherwise call the real function with the same arguments. The rest is unchanged.
4. `test_check_machine_notes_the_store_and_audit_log_together`: use the cases `(9e9, 6e9, 1)`, `(9e9, 6e9 - 1, 0)`, `(None, 15e9, 1)`, `(14e9, True, 0)`, `(15e9 - 1, None, 0)`. Also assert that a firing note contains `"15 GB"`.
5. `test_check_machine_reads_an_old_status_as_the_store_alone`: `16e9` gives 1 note, and `14e9` gives `[]`.
6. `test_check_machine_notes_a_big_store`: `store_bytes=16e9`.
7. New test, `test_a_linked_audit_folder_keeps_every_file_in_its_target(prune_env, tmp_path, monkeypatch)`:
   - Make a real folder `tmp_path / "target"` holding `audit.jsonl.2` (3 days old) and `audit.jsonl` (3 days old).
   - Make a symlink `tmp_path / "linked"` pointing to it.
   - Add an old event row. Run `prune_env.run(audit=str(tmp_path / "linked" / "audit.jsonl"))`.
   - Assert that both files in `target` still exist with their contents, `result["error"]` is non-empty and mentions `linked`, `result["audit_bytes"] is None`, and `result["last_prune"] is None`.
   - Then, with `module.AUDIT` patched to the linked path, `module.DB` and `module.STATUS` to the env's, `module.API` to the env's api url, and `time.time` set to `NOW` (as `_main` and its neighbours already do), assert that `main()` returns 1 and both target files are still there. Reuse the existing `_main` helper if it takes these through `monkeypatch`; read it first.
8. New test, `test_the_audit_step_deletes_through_the_folder_it_opened(prune_env, tmp_path, monkeypatch)`. It proves the check and the listing see the same folder:
   - Put an old `audit.jsonl.2` in the env's real audit folder, and an old `audit.jsonl.2` in `tmp_path / "elsewhere"`.
   - Wrap `prune_env.m.os.listdir` so that, on its first call with an int argument, it renames the audit folder to `tmp_path / "moved"` and symlinks the original folder path to `elsewhere`, then calls the real `listdir` on the same fd.
   - Run a due prune. Assert that `elsewhere/audit.jsonl.2` is still there, and that `moved/audit.jsonl.2` is gone, deleted through the fd.
9. The existing tests that must still pass unchanged:
   - a listed session keeps every audit file;
   - a session at the audit step keeps them;
   - `audit_bytes` counts the live log and rotated files, where a link named `audit.jsonl.3` isn't counted and a missing folder gives `None`;
   - the 50-minute due check;
   - the status-file and exit-code tests;
   - `test_prune_constants_agree_with_the_guard_and_stay_out_of_every_agent_write`;
   - `test_the_rendered_config_caps_the_audit_log`.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: covered above.
- The comment in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`: covered above.
- The `agentsh_prune.py` docstrings and the `Description=` in `agentsh_install.py`: covered above.
- The repo keeps no CHANGELOG.
- `README.md` and `SKILL.md` don't state these values.
- `docs/designs/agentsh-local-sandbox.md`, lines 1118 and 1144, say "over 10 GB". Those are records of earlier runs, and the engineer writes this run's entry there after the merge. The builder leaves that file alone.
- `app_docs/`: no edit. A write-up there for run 87b8ab19, if one exists, describes 6 hours, the 10 GB note and the followed link. The documenter should note that this run changes those values.

## Verification

From the repo root:

1. Run the skill suite the way the repo's quality check runs it:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. While building, run the focused file:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py -q -p no:cacheprovider`
3. Check by reading `git diff 3ee1333 --stat` and the diff:
   - Only these files changed:
     - `.claude/skills/sssf/scripts/agentsh_prune.py`
     - `.claude/skills/sssf/scripts/agentsh_install.py`
     - `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
     - `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`
     - `.claude/skills/sssf/references/config.md`
     - `.claude/skills/sssf/tests/test_agentsh_guard.py`
   - Nothing under `adws/`, `specs/` or `app_docs/` changed.
   - In `agentsh_guard.py`, only `STORE_WARN_BYTES` and `store_notes` changed.
   - In `agentsh_sssf.yaml`, only comment lines changed. `yaml.safe_load` of the baseline file and of the new one compare equal.
   - In `agentsh_prune.py`, `BATCH`, `PAUSE`, `LOCK_WAIT`, `PRUNE_EVERY`, `PRUNE_SLACK`, `ANALYZE_SECONDS`, the status keys, `skip_reason`, `sessions`, `fix_owner`, `write_status`, `main` and `_prune` are unchanged.
4. `grep -n "6 hours\|10 GB" .claude/skills/sssf/scripts/agentsh_prune.py .claude/skills/sssf/scripts/agentsh_install.py .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py .claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml .claude/skills/sssf/references/config.md` finds nothing about the store's retention or the note. Judge it by reading the lines, not by the exit status.

If this build runs under agentsh, the HTTP-backed prune tests may fail with `URLError` on the baseline too. Report that, rather than changing the tests to get around it. A suite run that can't read the repo's `.env` fails at collection on the baseline as well; report it the same way.

## For the engineer

These are protected or root-owned, so the builder doesn't touch them:

1. After the merge, sync the hub's own copies from the skill, as with 837bd01's "Update the hub to skill" commit: `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agentsh_sssf.yaml`.
2. Reinstall the prune on the host: `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project>`. Run the `sudo install` lines it prints, then `sudo systemctl daemon-reload`, then restart.
   - The installed `/usr/local/lib/sssf-agentsh/agentsh_prune.py` keeps 6 hours until you do.
   - The unit's `Description=` line shows in the diff.
   - The rendered `sssf.yaml` doesn't change, since only a comment changed.
3. If the store is past 15 GB from the 6-hour days, the note keeps firing after this lands, because deleting rows doesn't shrink the file. With no run going, do the reclaim in `config.md`.
4. The task asks for `audit_bytes` "from run F's rollout". The planner couldn't read `/var/lib/sssf-agentsh-prune/status.json` (permission denied), and no recorded figure exists in the repo. Read it with `sudo cat /var/lib/sssf-agentsh-prune/status.json`, then add the measured audit rate, and where it settles under 2 hours and the 1.2 GB cap, to `config.md`'s "The event store" and the yaml comment in a follow-up. Until then, the builder's text says the audit rate is unmeasured and capped at 1.2 GB.
5. Record run H in `docs/designs/agentsh-local-sandbox.md`.
