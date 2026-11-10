# Pin the agentsh build that refuses namespaces

Item 1 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Task file: `specs/tasks/guard-agentsh-pin.md`.

## Summary

`PINNED_SHA256` in the skill's `agentsh_guard.py` still holds build `41960715`'s sha256, so `check_machine` refuses every run under `local_guard: agentsh` on this machine, where `/usr/bin/agentsh` is the fork's `241cd393` on `sssf/confine`. This change moves the pin to that build and updates `config.md` to say which build a repo needs, why, and the new known limit on namespaces. Nothing else in the guard changes.

| | |
|---|---|
| Commit | `241cd393`, `fix(ptrace): refuse namespaces, mounts and chroot in tracees`, on `3918a619` |
| Version | `0.20.6-dev.fork+3918a619+ns.87c6994d` |
| sha256 | `ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce` |

## Threat model

The agent runs as the engineer's own Unix user. Under `local_guard: agentsh` it runs inside `agentsh wrap`, traced by a root-owned agentsh service with a default-deny policy; the text checks (`rules.py`, `secret_files.py`) stay on. The agent can edit any file in its workspace, including the project's `adws/` code, and can run any command the policy allows.

`check_machine` is the host-side gate before any agent starts. It checks that the service is active, that `/usr/bin/agentsh` hashes to `PINNED_SHA256`, and that the installed config and policies match what the checkout renders. The pin is what ties the guarantees in `docs/designs/agentsh-local-sandbox.md` to the bytes actually running. A wrong pin either refuses every run (today) or, if it named an older build, would accept a binary with the `tar -x` failure and the namespace hole: on `3918a619` and earlier, an agent could `unshare(CLONE_NEWUSER|CLONE_NEWNS)`, bind-mount any folder it can read over a folder in its workspace, and read through it, past every file read rule including the credential denies.

What this guard can't stop at its level: the pin is a constant in a file the agent can edit in the project checkout. The hub's install renders from the hub, and an edited project `adws/` makes the next run refuse with "differs" for config and policy, but a changed `PINNED_SHA256` in the project's own copy is read by that project's runner. That is an old hole, not one this change opens or closes. The hash check also can't tell whether the service is running the binary on disk (a replaced file with no restart).

## Baseline

`main` at `b4746ed` ("Put the hub on the text guard for the pin run"). Every finding is judged against it.

At the baseline, `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py:40` reads:

```python
PINNED_SHA256 = "3774b1170d447e8c935fb8c2d7421199b6d3fafc312d6f77de1bb2cd1c5db45d"   # build 41960715
```

and `.claude/skills/sssf/references/config.md:456` says the wrap needs agentsh `0.20.6-dev.fork+3918a619` or later.

## Bar

The change must:

- Set `PINNED_SHA256` to exactly `ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce`, with a comment naming `241cd393` on `sssf/confine`. A pin that doesn't match that sha256 is a broken promise.
- Say in `config.md` which build a repo needs (`0.20.6-dev.fork+3918a619+ns.87c6994d`, commit `241cd393`) and why, and add the namespace known limit.

The change must never:

- Touch `check_machine` or any other line of `agentsh_guard.py` beyond line 40's value and comment. Any other change to the guard is a finding.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline does otherwise. All earlier bars stand.
- Edit a protected path (see "For the engineer").

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- The fork's ninth patch for link targets (`f2ee6845`, `65e2d4fb`), which waits on a review. Relative symlinks stay denied; absolute link targets stay judged by text.
- The tool-config task, `specs/tasks/guard-agentsh-tool-config.md`: uv's and Corepack's config lookups above the repo, and agent-writable caches (`~/.cache/uv`, `~/.npm`, cargo's registry) that host tools reuse.
- The policy rules themselves, pi under the wrap, and sudo during the install.
- The pin living in an agent-editable project file (see Threat model), and the service possibly running a binary older than the one on disk.
- Whether agentsh's seccomp blocklist stacks with ptrace mode; the BPF prefilter injection fallback beyond what `87c6994d` covers.

## Files to change

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

Replace line 40 only. The new line must be exactly:

```python
PINNED_SHA256 = "ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce"   # build 241cd393 on sssf/confine
```

(Three spaces before `#`, as today.) No other line in the file changes. Do not touch the module docstring, `check_machine`, or anything else.

### 2. `.claude/skills/sssf/references/config.md`

a. In the paragraph at line 456 (section "Running local agents under agentsh — `local_guard`"), replace the sentence

> It needs agentsh `0.20.6-dev.fork+3918a619` or later, since earlier builds fail `tar -x` on files in subfolders.

with one that names the pinned build and says why, for example:

> It needs agentsh `0.20.6-dev.fork+3918a619+ns.87c6994d`, the fork's commit `241cd393`, which is the build SSSF pins: earlier builds fail `tar -x` on files in subfolders, and let an agent read any file it could read outside the wrap through a namespace of its own.

Keep the rest of that paragraph as it is.

b. In the "More known limits, each with what still applies:" list (starts at line 464), add one bullet, placed after the relative-symlink bullet (the one starting "A symlink with a relative target is denied"), for example:

> - Under the wrap, a tool that needs a namespace gets `EPERM`: `unshare` and `clone` that ask for one, `setns`, `mount` and the new mount API, `chroot` and `pivot_root` are refused in every traced process. That stops bubblewrap, Chrome's and Electron's sandboxes and rootless podman. `clone3` gets `ENOSYS`, which glibc answers by falling back to `clone`, so threads and forks work as before. Claude Code and pi don't use a namespace in SSSF's runs.

Match the doc's existing voice: short declarative sentences, backticks for names.

### Docs this makes out of date

- `.claude/skills/sssf/references/config.md` — covered above.
- The module docstring and help text in `agentsh_guard.py` don't name a build; no change.
- No CHANGELOG in the repo; none to update.
- `docs/designs/agentsh-local-sandbox.md` (line 76's "Which build" row and line 836) and `docs/open.md` say the pin still names `41960715` and refuses until this task lands. They become stale once this merges. They aren't protected, but they are the engineer's design record of state after merge, and the request doesn't ask for them; leave them to the engineer (listed under "For the engineer"). Do not edit them.
- `app_docs/`: never edit. If any write-up there names the `3918a619` requirement or the `41960715` pin as current, the documenter should note it now describes old behaviour.

## Verification

1. The pin is the exact line (this is the run's extra check; it exits 0 only when the line matches):

   ```
   grep -qxF 'PINNED_SHA256 = "ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce"   # build 241cd393 on sssf/confine' .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py
   ```

2. The guard diff is one line: `git diff main -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows exactly one `-` and one `+` line, both line 40. (Review step, not a check: it compares with git.)
3. `config.md` names `0.20.6-dev.fork+3918a619+ns.87c6994d` and `241cd393`, no longer says "`3918a619` or later", and the limits list has the namespace bullet with `EPERM`, bubblewrap, Chrome, Electron, rootless podman, `clone3` and `ENOSYS`.
4. The repo's own suite passes, as `quality.py` runs it: `uv run --with pytest --with pydantic --with pyyaml pytest tests -q -p no:cacheprovider` from `.claude/skills/sssf/`, in particular `tests/test_agentsh_guard.py`. Those tests monkeypatch `PINNED_SHA256`, so they don't see the value; no test changes are needed and none should be added that compare against the real `/usr/bin/agentsh`.
5. After the run (hub session, not the builder): the real `check_machine` against `/usr/bin/agentsh` on this machine, once the hub copy is synced.

## For the engineer

- `adws/adw_modules/agentsh_guard.py` is the hub's own installed copy, identical to the template at the baseline and protected. The hub's runs read this copy, so the hub keeps refusing under `local_guard: agentsh` until it gets the same one-line change, through `sssf-update` / `install.py --update` from the skill or by hand. Same for sssf-sandbox's copy.
- Check the real `check_machine` against `/usr/bin/agentsh` on this machine after the sync, then set the hub's `local_guard` back to `agentsh` in `adws/adw_sssf_config/repo.yaml` (protected).
- Update `docs/designs/agentsh-local-sandbox.md` (line 76's "Which build" row, line 836, the Next list) and `docs/open.md` to say the pin names `241cd393`.
