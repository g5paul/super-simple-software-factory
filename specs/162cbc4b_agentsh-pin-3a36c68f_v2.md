# Pin agentsh build 3a36c68f and bring the agentsh docs in line

Item 5 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Request: `specs/tasks/guard-agentsh-pin-races.md`.

## Summary

Move `PINNED_SHA256` in the skill's template guard to the sha256 of build `3a36c68f`, which the engineer installed on 2026-11-10, and update the docs that describe the old build: `config.md`'s agentsh section, the two agentsh rows in `docs/guide/install.md`, and one line of `docs/concepts.md`. The only code change is that constant and its comment.

| | |
|---|---|
| Commit | `3a36c68f` on `sssf/races` (`dea39254` and `8ac79c33` under it, on `sssf/patched`) |
| Version | `0.20.6-dev.fork+3a36c68f` |
| sha256 | `0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886` |

## Threat model

The agent runs as the engineer's own user. Under `local_guard: agentsh` it runs inside `agentsh wrap`, and the service judges its execs, opens, links, renames and connects against the policy. What the pin does is make sure the binary at `/usr/bin/agentsh` is the reviewed build. A build that doesn't judge `renameat`, which `mv` makes through glibc 2.39, lets an agent move a file outside every allow into its workspace and read it there. Build `241cd393`, the current pin, has that hole and also skips `mknod` and `lchown`. It judges a link path whose folder is missing as text, and a link's target can be rewritten after the check.

What the pin can't stop at its level, since the agent is the engineer's user: it reads the binary on every run start, but it can't see a service still running an older binary that was replaced on disk. It also can't see an agent's edit to the project's own copy of `agentsh_guard.py`, which a later run in that project would load. Both are old holes (Next item 1). A tool that follows a link outside the wrap isn't judged by agentsh at all.

## Baseline

`main` at `fa4eac1` ("Add spec for pinning agentsh build 3a36c68f"). Every finding is judged against it. At the baseline, `PINNED_SHA256` is `ee6cd37e…20bce` (build `241cd393` on `sssf/confine`) in both `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` and the hub's own `adws/adw_modules/agentsh_guard.py`, and the two files are identical.

## Bar

The change must:

- set `PINNED_SHA256` in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` to exactly `0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886`, with a comment naming build `3a36c68f` on `sssf/races`. Any other value is a broken promise.
- leave the rest of `agentsh_guard.py` byte-for-byte as at the baseline. Any change to `check_machine` or the rest of the guard beyond that one line is a finding.
- make `config.md` promise nothing the probes in the request didn't show.

It must never, where the baseline does otherwise: keep an agent file, run agent code, or delete or overwrite an engineer file. The earlier bars stand.

## Out of scope

This change leaves these holes open. A reviewer should file them as old holes, not as findings against this change:

- The pin check's two old holes: an edit to the project's `agentsh_guard.py`, and a service still running an older binary (Next item 1).
- The tool-config task (uv's config lookup above the repo, Corepack's `package.json` lookup).
- The policy rules, pi under the wrap, sudo.
- The fork's code, which was reviewed and tested outside this chain.
- The hub's own `adws/adw_modules/agentsh_guard.py`, which is protected. See "For the engineer".
- `docs/designs/agentsh-local-sandbox.md` and the guard-run write-ups under `docs/designs/agentsh/` record the evidence as it was gathered. Don't edit them.

The fork, not the pin, decides the following limits. `config.md` writes them down (or keeps them), and this change doesn't close any of them:

- **A link is judged only when it's made.** A link moved later, or a folder on its path swapped for a link, can lead somewhere else. Inside the wrap every read is judged at the file it reaches, so only a tool that follows the link outside the wrap is exposed.
- **Case-folding folders.** On a folder that ignores case, such as `/mnt/c` under WSL, `.SSH` reaches `.ssh`, and a rule naming `.ssh` misses it.
- **Targets that fail closed.** A link target that passes through `/proc` or `/dev/fd`, or holds `..` after a folder that doesn't exist, is denied even where the file it would reach is allowed. Each denial counts as a file denial.
- **The workspace-root link** (kept from the baseline). An agent can replace its workspace root with an absolute link to a folder it made, and host git run in the workspace would follow it. The after-call guard's swapped-directory check is expected to stop the run, but this hasn't been run under the service.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

Replace line 40, and only line 40:

```python
PINNED_SHA256 = "ee6cd37e03bdd1461cb1c622a15adcf9264c19bc27b5301c2d94355b27120bce"   # build 241cd393 on sssf/confine
```

with:

```python
PINNED_SHA256 = "0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886"   # build 3a36c68f on sssf/races
```

Don't touch anything else in the file: no docstring, no message, no `check_machine` line.

### 2. `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh — `local_guard`"

**a. Opening paragraph** (the one starting "It's opt-in, and tested so far only on Python repos"). Remove the sentence "Making a venv needs a relative link, which agentsh denies (below), so `uv venv`, `uv run --with` and `uvx` fail, as do npm and bun on any package with a `bin`." Keep the sentence about uv's config lookup and Corepack, since those still break. "Two gaps break other work" still counts correctly after the removal: the uv lookup and Corepack's lookup. Replace the build sentence with:

> It needs agentsh `0.20.6-dev.fork+3a36c68f`, the fork's commit `3a36c68f` on `sssf/races`, which is the build SSSF pins: earlier builds fail `tar -x` on files in subfolders, let an agent read any file it could read outside the wrap through a namespace of its own, and let it move a file it may not read into its workspace with `mv`, then read it there.

Leave the rest of the paragraph ("A pi roster has no policy yet. Keep `local_guard: text`…") as it is.

**b. "More known limits" list.** Remove the bullet starting "A symlink with a relative target is denied, wherever it's made." In its place put:

> - A link's target is judged as the file it leads to, resolved from the link's folder through any links on the way, so a relative link in an allowed place works, as do `uv venv` and the links in npm's `node_modules/.bin`. A target that passes through `/proc` or `/dev/fd`, or holds `..` after a folder that doesn't exist, is denied. It fails closed. `worktree_setup` runs outside the wrap and isn't affected. Each denial counts as a file denial, not a refusal.

Remove the bullet starting "An absolute link target is judged by its text against the other rules." The bullet above replaces it.

Add two bullets, placed right after the new link bullet:

> - A link is judged when it's made. A link moved later, or a folder on its path swapped for a link, can lead somewhere else. That matters only to a tool that follows it outside the wrap, such as your own editor or host git, since every read inside the wrap is judged at the file it reaches.
> - On a folder that ignores case, such as `/mnt/c` under WSL, `.SSH` reaches `.ssh`, and a rule naming `.ssh` misses it.

Keep the bullet "An agent can replace its workspace root with an absolute link…" as it is. Don't change any other bullet or paragraph. Keep the wording above as close as you can. It's worded to claim no more than the request's probes showed: refused `mv`, `rename`, `renameat`, `mknod`, and canary links, both absolute and relative. Working relative links in the workspace, `uv venv`, and npm's `.bin`. Don't add a claim about `bun` or `uvx` working, which weren't probed.

### 3. `docs/guide/install.md`

- Line 33, the agentsh row in the tools table: change the version `0.20.6-dev.fork+3918a619+ns.87c6994d` to `0.20.6-dev.fork+3a36c68f`. Leave the rest of the row as it is.
- Line 47, the pinned-versions row: change it to
  `| The agentsh build | sha256 `0e4430b5…40886`, commit `3a36c68f` on the fork | `PINNED_SHA256` in `agentsh_guard.py` |`

### 4. `docs/concepts.md`, section "Local guards: text and agentsh"

The bullet "uv, pnpm and venvs fail under it in some layouts, which [config.md](…) lists with the other known limits." becomes "uv and pnpm fail under it in some layouts, which [config.md](../.claude/skills/sssf/references/config.md) lists with the other known limits." Venvs no longer fail. Keep the link target exactly as it is now.

### Docs this change makes out of date

These are covered by steps 2–4: `config.md` (build, venv sentence, three link limits, case-folding limit), `docs/guide/install.md` (version row, pin row), `docs/concepts.md` (venvs bullet). There's no CHANGELOG. The guard's docstrings don't name a build. No help text names the build. Nothing in `app_docs/` needs editing. For the documenter: an earlier write-up that says relative links are denied under agentsh, or that pins `241cd393`, now describes the old behaviour, so note that it's been replaced.

## Testing

No test changes. `.claude/skills/sssf/tests/test_agentsh_guard.py` monkeypatches `PINNED_SHA256` (lines 194, 335, 337), so it never sees the real value and passes unchanged. Don't add a test that compares the constant with `/usr/bin/agentsh`: the hub session checks the real `check_machine` against the installed binary after the run.

## Verification

Run from the repo root:

1. `grep -qxF 'PINNED_SHA256 = "0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886"   # build 3a36c68f on sssf/races' .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` exits 0.
2. `git diff --numstat -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` prints `1	1` for the file: one line added, one removed, both line 40. This is for you and the reviewer to read. It isn't an automated check, because SSSF won't run a git comparison as one.
3. `grep -n -E "241cd393|3918a619|ee6cd37e" .claude/skills/sssf/references/config.md docs/guide/install.md docs/concepts.md .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` finds nothing (exit 1).
4. `grep -n -E "relative target is denied|judged by its text|venvs fail" .claude/skills/sssf/references/config.md docs/concepts.md` finds nothing.
5. `grep -c "0e4430b5…40886" docs/guide/install.md` prints 1.
6. The repo's quality checks pass, including the skill tests (`uv run --with pytest --with pydantic --with pyyaml … pytest tests -q -p no:cacheprovider`, as `adws/adw_modules/quality.py` runs them), and `test_agentsh_guard.py` in particular.

## For the engineer

These paths are protected, so the builder doesn't touch them:

- `adws/adw_modules/agentsh_guard.py`, the hub's own install of the guard, still pins `ee6cd37e…` at line 40. As with build `241cd393` (commit `0951671`), bring the new pin in with the hub's SSSF update after this merges (`just sssf-update`), which also moves `adws/adw_sssf_config/stamp.json`. Until then the hub's own runs under `local_guard: agentsh` keep refusing with "not the pinned build".
- Set the hub's `local_guard` back to `agentsh` in `adws/adw_sssf_config/repo.yaml` after that update, then run the real `check_machine` against `/usr/bin/agentsh`.
