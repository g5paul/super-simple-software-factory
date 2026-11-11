# Keep 2 hours of agentsh's events, warn at 15 GB, and don't follow a linked audit folder

Start from `main`, which has run 87b8ab19 merged as a182d1f. It set `KEEP_SECONDS` to 6 hours from a measured 1.4 GB an hour. The store grew faster once chains ran back to back on 2026-11-11: 2.8 GB at 10:10, about 5.7 GB at 11:10, 8.2 GB by 12:00 and 8.65 GB at 12:20. After a reset at 13:26 it reached 4.58 GB at 14:50 and 5.95 GB at 15:29, about 3.3 GB an hour with chains running back to back. At that rate 6 hours plus up to an hour of prune lag levels off at about 20 to 23 GB, and the 10 GB note fires for most of a busy day.

## The problem

1. **6 hours keeps about three times what fits under the note.** Nothing reads an event after its call ends: the guard's poll reads only its own session, and the prune deletes nothing while the service lists a session. The longest call on 2026-11-11 was run 361de0f2's 31-minute bypass review.
2. **The 10 GB note fires on a healthy store.** It was set before anyone measured the rate. On a store that size the planner's slowest poll was still under 5 s, but at 5.7 GB one poll took 3.9 s, so the note should still fire well before the limit stops a run.

3. **The prune follows a linked audit folder.** Run 87b8ab19's bypass review, `adws/adw_data/sessions/87b8ab19/context_handoff/bypass_review_1.md`, broken promise 1: `rotated()` lists `os.path.dirname(audit)` with `os.listdir`, so if `/var/lib/agentsh` were a symlink the prune would delete old `audit.jsonl.<N>` files in its target. The spec promised it follows no link. No agent can make that link, since the folder sits in root's `/var/lib`.

## What should change

1. **`KEEP_SECONDS` is 2 hours** in the prune, and the rotated-audit-file age with it. The text that says 6 hours says 2.
2. **The note fires at 15 GB** of store and audit together, in `check_machine`.
3. **The audit step refuses a linked folder.** It works on the audit folder only when that folder is a real directory and not a link, opened so the check and the listing see the same folder (for example `O_DIRECTORY | O_NOFOLLOW` and `dir_fd` for `lstat` and `unlink`). A linked or missing folder deletes nothing and records why, as other errors are recorded.
4. **`config.md` and the yaml comment** give the measured rates, the store's and `audit_bytes` from run F's rollout, and the level each settles at.

## The bar

The earlier bars stand. Nothing else changes: the session skip, the batches, the rotation keys, the status keys, the exit codes, the 50-minute due check, pi's loop, and every refusal and stop. A linked audit folder keeps every file in its target.

## Testing

The tests at the old values move to the new ones: a row 2 hours and one second old is deleted and one a second younger is kept; the note at 15 GB and not below it; an audit folder that is a symlink to a folder holding an old `audit.jsonl.2` keeps it.

## Out of scope

Everything run F's spec puts out of scope.
