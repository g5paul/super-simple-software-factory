# Keep test data and secret probes clear of the rules, and make the store note name the audit files

Start from `main`, which has run c0f9d9d8 merged as 6c829e6. These are Next items 20 and 21 in `docs/designs/agentsh-local-sandbox.md`.

## The problem

1. **The rules refused 11 commands on 2026-11-11, in 8 guard runs, and none was an agent trying to get past a guard.** Each refusal counts toward the three-refusal stop, and run c535b28d's bypass reviewer had two. The trace's `rule_refused` events show three shapes:
   - **Inline scripts that carry forbidden text as data, 7 refusals.** A builder edited a test file with `python3 - <<'EOF'` (runs 0463a6c3 and 87b8ab19), or wrote one with `cat > /tmp/new_tests.py <<` or `cat >> test_agentsh_guard.py <<` (runs 955ca723 and 361de0f2). Bypass reviewers patched a scratch harness with a `python3 - <<'PY'` whose comment named `.env` (run 0463a6c3), ran a `python -c` holding a `git push` candidate, and grepped a test file for `\.env` (both run c535b28d). The builder and reviewer prompts already say "Write files with your file tool, never through the shell with a heredoc or `echo`". Agents read that as being about new files, and still edit files and run checks through inline code.
   - **Real secret probes, 2 refusals.** `cat .env && echo readable` (run 361de0f2's bypass reviewer) and `ls -l .env; python3 -c "open('.env').read()"` (run 87b8ab19's builder). The rule refused both before they ran. No prompt says to probe a decoy.
   - **Copying the repo to test outside it, 2 refusals.** `rsync ... --exclude '.env*'` (run b684482d) and `rsync -a --exclude .env ...` (run c0f9d9d8). The guard briefs tell the builder to run the suite from a copy outside the repo, and naming `.env` to leave it out trips the secret-file rule.
2. **The 15 GB note's advice covers only the store.** `store_notes()` in `agentsh_guard.py` adds the store and audit sizes, then tells the engineer to move `events.db` aside. On 2026-11-11 the first status with `audit_bytes` read 3.73 GB of audit log against a cap near 1.2 GB. Moving the store aside leaves all of that.
3. **`config.md` doesn't state two limits run c0f9d9d8's review found.** The prune opens the audit folder with `O_NOFOLLOW`, which checks only the last part of the path, so a link above it, such as `/var/lib`, is still followed. The store path, `events.db`, follows a link through `sqlite3.connect` and `os.stat`. Both paths are root's, and no agent can make either link.

## What should change

1. **The builder and reviewer prompts cover editing and inline code.** In the shipped `templates/prompt_engineering/builder/system.md` and `reviewer/system.md`, and in the hub's `adws/adw_data/prompt_engineering/builder/system.md` and `bypass_reviewer/system.md`, the file-tool line says: create and edit files with the file tools, and when a script or check carries text that names a forbidden command or a secret file, write it to a file with the file tool and run that file. The line keeps "The rules apply to what you run, however you run it." The other prompts that carry the line keep it as it is.
2. **Those four prompts say how to test a secret-file rule.** Probe a decoy the agent makes in a scratch repo it creates. The repo's own secret files stay closed to every command, a probe included.
3. **The guard chain's build brief says how to copy the repo.** `build_brief()` in `adws/adw_guard_change.py` gives the copy command: the files `git ls-files --cached --others --exclude-standard` lists, so `.gitignore` leaves `.env` out and the command never names it. The planner checks that `.env` is ignored in the hub (`.gitignore` line 112 today).
4. **The store note names the bigger part.** The note gives both sizes. When the audit log is the bigger part, its advice is for the rotated `audit.jsonl.<N>` files: the prune deletes each one once it is 2 hours old, and the note says how to free the space sooner with no run going. When the store is the bigger part, the advice stays as now. The note never refuses or stops.
5. **`config.md`, in "Running local agents under agentsh"**, lists the two limits in problem 3.

## The bar

The earlier bars stand. In every mode:

- No rule, pattern or check changes. The text guard is frozen, and no regex is added or widened.
- Every refusal and stop at the baseline stays as it was.
- The prompts change only in the lines items 1 and 2 name, and no new line grants an agent anything the baseline prompts deny.
- The store note stays advice: it prints and traces as now, and its sizes come from the status file as now.

Any change beyond the five above is a finding, and so is a `config.md` or prompt line that promises more than the code does.

## Testing

Unit tests: the store note with the store bigger, the audit log bigger, and only one size present; the copy command from `build_brief()`, run in a temp git repo with a `.gitignore`d `.env`, a tracked file, a modified tracked file and an untracked file, copies all but `.env`. The tests never touch `/var/lib/agentsh` or the real `.env`.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The runner uses the prompts and `build_brief()` its checkout had when it started, so this run's builder still sees the old lines and the old brief. The builder runs the suite from a copy outside the repo, as earlier runs did.

## Out of scope

The text rules and their false positives on a command's data: the text guard is frozen, and agentsh replaces it. The links above the audit folder and at the store path: this run documents them and doesn't fix them. Prompts other than the four in item 1. pi's policy.
