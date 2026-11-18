# Narrow the host commit check: the planner's grant and path spellings (v2)

This replaces `specs/8dee7020_narrow-host-check.md` after spec review 1. What changed: an agent that runs again does not get its grant back (the old bar 4 and `agent_starts` are gone, and `agents.py` is not touched); the 8.3 short-name match is a check on the name's shape alone; the fold has one form, not two; rows 5 to 7 of the spellings table say more about defaults.

Follows `specs/tasks/guard-host-commit-check.md` and `specs/755d88ad_host-commit-check_v2.md` (merged as a0dd48f); their bars carry over. Closes two P2 rows in `docs/open.md`, found by the `bypass-reviewer` subagent (probes a1, a2, v07 in `adws/adw_data/sessions/755d88ad/bypass_probes/`, in the main checkout, outside the worktree).

## Goal

1. After an `only` commit passes, an agent that ran before it no longer passes a path inside that commit's `only` entries. Its grant elsewhere stays.
2. `permitted()` treats a path as protected in every spelling a case-insensitive checkout, or `just`, takes for a protected entry. Only the refusing side folds.

All module paths are under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

## Threat model

- The agent runs as the engineer's user. In a VM run it controls the whole VM: every in-VM check, the clone's `.git`, and each answer the VM gives. It cannot reach the host's `.git`; the host judges commits from objects its own `index-pack` hashed. So the host check is the last check, and what it passes lands on the engineer's branch.
- The host can't tell which agent made a change. A path passes on the grant of any agent in `run.ran` (755d88ad, decision 1). This change can only shorten that list per path. It cannot stop a change inside the `writes` of an agent whose grant still holds, whoever made it.
- The host check runs before any checkout, and can't know which filesystem the engineer will check out on, or which names a tool there (`just`, Claude Code, pi) reads. So it folds always.
- In local mode the agent controls the `.git` the check reads; the limits in 755d88ad's threat model stand unchanged.

## Baseline

`main` at `e224f00`. Every finding is judged against that commit. There: `run.ran` is cleared only after a full commit, so `commit_build` in `.claude/skills/sssf/templates/adws/adw_simple_sdlc.py` judges the builder's commit with `[planner, builder]` and a builder change to `specs/old.md` passes (a1); `permitted()` compares exactly, so `JUSTFILE`, `ADWS/adw_x.py` and `.CLAUDE/settings.json` pass for the builder (v07).

## Bar

Against `e224f00`: no agent file kept on the host branch, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise.

1. **a1 and a2.** After a plan commit of `specs/`, a builder commit that changes `specs/old.md` is refused, in local mode and through the VM stand-in. The branch is back at the commit before (local) or at `synced` (VM), and the `PermissionBreach` names the path.
2. **The grant outside `only` holds.** Planner `writes` `["specs/", "docs/plans/"]`, commit `only=["specs/"]`: the planner's uncommitted `docs/plans/x.md` passes in the next full commit; a builder change to `specs/` in that same commit is refused.
3. **More than one `only` commit.** Two `only` commits with different entries, then a full commit: each entry is judged without the agents that ran before its commit.
4. **v07 and each listed spelling** (table below) is refused by the host check for the shipped builder, in local mode and through the VM stand-in. The same commit with the exact name is refused as on the baseline.
5. **No new pass.** Every path the baseline's `permitted()` refuses for an agent, the head refuses: the shipped rosters' agents, an agent with `writes: None`, an agent whose `writes` names protected paths.
6. **No false stop.** Every existing test passes. These pass as on the baseline: the plan commit, a builder's commit inside its `writes`, the documenter's `**/*.md`, the session files, the ticket file `tickets.mark_done` writes.
7. **Nothing else changes.** When every commit passes: same branch, same trace events, same saved patch.

## The spellings

Git's part was checked on this machine with git 2.43.0 on ext4: a commit holding `justfile::$DATA`, `adws\x.py`, `justfile.`, `JUSTFI~1` and `just<U+200C>file` checks out with exit 0 each. Git refuses none of these outside `.git`, so each is folded. The `git-config(1)` lines were read from the installed manual. The source citations are from memory: the builder confirms file and function names against git's source if it is at hand, and otherwise marks them "(not re-read)" in the code comment.

| # | Spelling | Test paths | What makes it the same file | Git |
|---|---|---|---|---|
| 1 | ASCII case | `JUSTFILE`, `ADWS/adw_x.py`, `.CLAUDE/settings.json`, `SPECS/old.md`, `glossary.md` | APFS, HFS+, NTFS, FAT, a Linux casefold folder; `just` 1.59 on any filesystem | `git-config(1)`, `core.ignoreCase`. Checkout opens the path through the filesystem (`entry.c`, `write_entry`). No refusal |
| 2 | Unicode case folding | `juſtfile` (long s), `.githooKs/pre-commit` (Kelvin), `.pı/x.json` (dotless i, whose uppercase is `I`) | APFS and a Linux casefold folder fold by Unicode case folding; NTFS compares by its `$UpCase` table. Whether `$UpCase` maps U+0131 to `I` was not verified, so it is folded anyway | Git folds ASCII only: `utf8.c`, `next_hfs_char`; `name-hash.c`. No refusal |
| 3 | Normalization, NFC and NFD | With `docs/café/` added to `protected_files`: `docs/café/x.md`, and the reverse. `.githooKs` also covers it (U+212A decomposes to `K`) | HFS+ stores NFD; APFS and a Linux casefold folder compare normalized | `git-config(1)`, `core.precomposeUnicode`; `compat/precompose_utf8.c`. No refusal |
| 4 | HFS-ignored code points | `just‌file` | HFS+ ignores U+200C–200F, 202A–202E, 206A–206F, FEFF | `utf8.c`, `is_hfs_dotgit`; `core.protectHFS` (true on macOS only) refuses just `.git` and the `.git*` files |
| 5 | Default-ignorable code points | `just­file` (soft hyphen) | A Linux casefold folder (ext4, f2fs, tmpfs) compared with ignorable code points dropped (kernel `fs/unicode`, from memory). Recent kernels may have stopped ignoring them (not re-read). Folded either way: it can only refuse | Git has no check |
| 6 | NTFS trailing dots and spaces | `justfile.`, `justfile ` (trailing space), `adws./x.py`, `adws /x.py` | Win32 drops them from each component | `path.c`, `is_ntfs_dotgit`. Git for Windows refuses them (`compat/mingw.c`, `is_valid_win32_path`) while `core.protectNTFS` is on, its default there: `git-config(1)` says "Defaults to true on Windows, and false elsewhere". Linux git on `/mnt/c` has no such check, and the setting can be turned off, so folded |
| 7 | NTFS streams | `justfile::$DATA`, `adws::$INDEX_ALLOCATION/x.py` | The default stream is the file itself | Refused on Windows while `core.protectNTFS` is on, its default there (`read-cache.c`, `verify_path`). Elsewhere, and with it off, not refused, so folded |
| 8 | Backslash | `adws\x.py` | A separator on Windows | `verify_path` refuses it on Windows while `core.protectNTFS` is on; with it off `is_dir_sep` takes it as a separator. Folded |
| 9 | NTFS 8.3 short names | `CLAUDE~1/settings.json`, `PI~1/x.json`, `MCP~1.JSO`, `GITHOO~1/pre-commit`, `SSSF~1.JUS`, `LOCAL~1.JUS`, `CL1A2B~1/settings.json` (the hash form), `JUSTFI~1` | NTFS gives a long name an alias: up to 6 characters of the base, `~N`, 3 of the extension; after four clashes, 2 characters, 4 hex digits, `~N` | `path.c`, `is_ntfs_dotgit` (`git~1`) and `is_ntfs_dot_generic` (the hash form); `core.protectNTFS` covers `.git*` only. No refusal for other names |

The brief's `JUSTFI~1` is not a name NTFS makes: `justfile` is already a valid 8.3 name and gets no alias. It is refused all the same, since the match goes by the name's shape.

## Design notes for the reviewer

- **The grant.** One new piece of state, `run.taken`: `(agent, entry)` pairs, "this agent's grant inside this `only` entry was committed". It is the least that meets bars 2 and 3: clearing `run.ran` fails bar 2, and one list of taken entries for all agents fails bar 3 (the second commit's agent would lose `specs/` before its work there was committed). No tree is read. The pairs last until the next full commit passes.
- **An agent that runs again does not get its grant back.** No shipped chain runs an agent between an `only` commit and the next full commit: the one `only` commit is `commit_plan` in `adw_simple_sdlc.py`, and the planner does not run after it. The hub's guard chain revises its plan before its plan commit. A chain that did would be refused: a known false stop, listed in Out of scope and in `config.md`.
- **One fold form.** `casefold(upper(x))` between two NFD passes. Checked here over every code point of Python 3's Unicode 15.0 data: any two code points equal under `NFD(casefold(NFD(x)))` are equal under it too, so it loses nothing against plain case folding, and it also joins what only uppercasing joins (U+0131 with `i`). A test repeats the check.
- **Short names by shape.** A component shaped like an 8.3 alias counts as the same as any entry component in its position. That needs no table of how NTFS builds an alias, and an agent can't steer around it.
- **The fold only adds refusals.** `permitted()` gains entries in its `fixed` and `guards` lists and changes nothing else. A longer `fixed` list can only reach the early `return False`. A `writes` entry `w` unlocks a path only when `_matches(path, w)` and `_names(w, g)` hold exactly, and those two together mean the path matches `g` exactly, so a `g` matched only by folding never unlocks anything. With `guards` newly non-empty the result needs a `w` that matches the path, which passed before. So no path passes that the baseline refuses.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: the grant

- `_inside(path, entries) -> bool`: `path == e`, or `e` ends in `/` and `path.startswith(e)`, for some `e`. Use it for the existing `only` test in `judge_commits` too (same rule, literal).
- `judge_commits(..., taken=())`: the agents clause becomes `any(permitted(path, a, cfg) and not _inside(path, [e for t, e in taken if t == a]) for a in agents)`. Nothing else in it changes.
- `commit_passed(run, only) -> None`: no `ran` attribute: return. `only` None: clear `run.ran`, and `run.taken` when the run has one. Otherwise add `(a, e)` for each `a` in `run.ran` and each `e` in `only`, skipping pairs already there (make `run.taken` with `getattr` when a test's stand-in run has none).
- `check_local_commit`: pass `taken=list(getattr(run, "taken", ()))`; on a pass call `commit_passed(run, only)` in place of the clear. Update its docstring.
- Module docstring: the commit-check paragraph says an `only` commit ends the earlier agents' grant inside its entries, until the next full commit.

### 2. `permissions.py`: the fold

Beside `_in_git_dir`:

- `_IGNORED`: Unicode's Default_Ignorable_Code_Point ranges, a superset of `_HFS_IGNORED`: U+00AD, 034F, 061C, 115F–1160, 17B4–17B5, 180B–180F, 200B–200F, 202A–202E, 2060–206F, 3164, FE00–FE0F, FEFF, FFA0, FFF0–FFF8, 1BCA0–1BCA3, 1D173–1D17A, E0000–E0FFF.
- `_fold_part(part) -> str`: NFD, drop `_IGNORED`, `upper()`, `casefold()`, NFD again, cut at the first `:`, `rstrip(". ")`.
- `_fold(path) -> list[str]`: replace `\` with `/`, split on `/`, fold each part, drop empty results.
- `_SHORT = re.compile(r"[^.~]{1,6}~[0-9]+(?:\.[^.]{0,3})?")`: the shape of an 8.3 alias, matched against a folded component with `fullmatch`.
- `_folds_to(path, entry, *, bare=False) -> bool`:
  - an entry with `*` or `?` that does not end in `/`: `_glob("/".join(_fold(entry))).fullmatch("/".join(_fold(path)))`. No short-name match;
  - else compare component by component. A path component is the same as the entry's when the folded strings are equal, or the path's fullmatches `_SHORT`. A directory entry (ends in `/`) needs the path to have more components than the entry, or as many when `bare`. A file entry needs the same count.
- `permitted()`: two lines change.
  - `fixed`: add `or _folds_to(path, p, bare=True)`.
  - `guards`: `_matches(path, p) or _folds_to(path, p)`.
  - The always-writable test, every `_matches(path, w)`, `_names` and the last two lines stay as they are. Update the docstring and the comment above `FACTORY_FILES` (matched in any spelling a checkout takes for them).
- `_in_git_dir` stays as it is.

### 3. Callers

- `.claude/skills/sssf/templates/adws/adw_modules/runner.py`, `Run.__init__`: `self.taken: list = []`, with a comment; fix the `ran` comment.
- `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`, `commits_out`: pass `taken=list(getattr(self.run_, "taken", ()))` to `judge_commits`, and replace the final clear with `permissions.commit_passed(self.run_, only)`. Update the module docstring's commit bullet if it mentions `run.ran`.
- `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`: docstring of `commit_all` only, one sentence on the grant.
- `.claude/skills/sssf/templates/adws/adw_modules/agents.py` is not changed.

### 4. Tests

Config from `.claude/skills/sssf/templates/sssf.config.yaml` always; a test that needs another `writes` or `protected_files` entry copies the loaded config and changes that field. No real VM. A test adds an agent to `run.ran` by appending it, as `agents.execute` does. No test here passes a large argument, so none needs `host_only`; mark one that does.

- `.claude/skills/sssf/tests/test_commit_check.py`:
  - bar 1 locally through `git_helper.commit_all(run=...)`: plan commit with `ran=[planner]`, append the builder, change `specs/old.md`, full commit refused, `HEAD` back at the plan commit, path named. And a2 with the builder alone;
  - bar 2 and bar 3 as written. For bar 3 use three agents: A, `only=["specs/"]`, B, `only=["docs/plans/"]`, C, then full commits that show `specs/` passes on B's grant and not on A's, and `docs/plans/` on neither A's nor B's;
  - the known false stop, pinned: planner, plan commit, the planner's new `specs/x_v2.md`, a second `only=["specs/"]` commit is refused; after a full commit passes, `run.ran` and `run.taken` are empty and the planner's next plan commit passes;
  - `commit_passed` on a stand-in with no `taken` attribute and on one with no `ran`;
  - bar 4 locally: one parametrized test over every test path in the table, committed in a real repo and judged with `ran=[builder]`; and each exact name refused.
- `.claude/skills/sssf/tests/test_factory_files.py`:
  - `permitted()` refuses every table path for the builder, the documenter and a `writes: None` agent; the NFC/NFD pair with the added entry;
  - not folded: `src/JUSTFILE`, `justfiles`, `adwsx/a.py`, `docs/just~1`, `src/a~1.py`, `notes~`, `README.md`, `src/app.py` pass for the builder as on the baseline;
  - bar 5: a copy of the baseline's `permitted()` body in the test (exact `_matches` only), run against the head over the table paths, their exact names, and ordinary paths, for every shipped agent, a `writes: None` agent, and an agent with `writes: ["**", "justfile", "adws/", "specs/"]`. Assert no path the copy refuses passes, and that the exact names and ordinary paths get the same answer from both;
  - an agent whose `writes` names `justfile` still writes `justfile` and is refused `JUSTFILE` (decision 2);
  - `_HFS_IGNORED <= _IGNORED`;
  - the one-form check: over every code point but the surrogates, code points equal under `NFD(casefold(NFD(x)))` have equal `_fold_part` results where neither is empty (about 2 s here).
- `.claude/skills/sssf/tests/test_sandbox_remote.py`, with the `jbox` fixture the 755d88ad tests use:
  - bar 1: `ran=[planner]`, `commit(..., only=["specs/"])` adding `specs/old.md` passes; append the builder; a change to `specs/old.md` committed in full is refused, host branch at `synced`, `sandbox-refused.bundle` kept;
  - bar 3 through `commit`;
  - bar 4: `JUSTFILE`, `ADWS/adw_x.py`, `.CLAUDE/settings.json` and one path per table row;
  - bar 7: a passing plan commit then build commit give the same `sandbox_commits` events as before, and `run.ran` and `run.taken` are empty after the full one.

### 5. Docs

- `.claude/skills/sssf/references/config.md`, "Write permissions, `writes` and `protected_files`":
  - replace the sentences from "`run.ran` is cleared after a full commit passes" to "(the in-call check still stops it)": the planner's grant ends at the plan commit for the paths inside that commit's `only` entries, and stays for what it may write elsewhere until the next full commit. An agent that runs again before that full commit does not get the ended part back, so a chain that has the planner revise a spec after the plan commit is refused;
  - add: a protected path is matched in any case and in the spellings a case-insensitive disk takes for it (Unicode case and normalization, ignorable code points, trailing dots and spaces, `:stream`, `\`), and a name shaped like an 8.3 short name (`CLAUDE~1`, `notes~1.txt`) counts as any protected name in its place; `writes` entries are matched exactly, so `writes: [justfile]` does not unlock `Justfile`, and a repo whose recipe file is spelled `Justfile` cannot unlock it;
  - in the known limits, add the Out of scope items below that are new, and drop any limit this closes.
- `docs/open.md`: close the two P2 rows (the planner's grant; case variants), the way the file records the rows 755d88ad closed. In the P3 "Smaller factory-files edges" row, drop the clause on `.MCP.json` and `Local.just`. Add `.justfile` as an old hole if no row has it.
- `docs/designs/remote-sandboxes.md`: one "As built" line.
- `CHANGELOG.md`: an entry, if the repo keeps one.
- For the documenter: an `app_docs/` write-up of 755d88ad that says the planner's grant lasts until the full commit, or that paths are matched by exact case, now describes old behaviour.

## Verification

From `.claude/skills/sssf`:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -m 'not pushes and not host_only' tests/test_commit_check.py tests/test_sandbox_remote.py tests/test_factory_files.py tests/test_factory_holes.py tests/test_agent_commits.py tests/test_git_dir_guard.py tests/test_failed_call_rollback.py tests/test_tickets.py tests/test_adw_tickets.py`

All pass. The whole suite is verify's. Every new test is in a file the suite already runs, so the plan adds no extra check.

## Out of scope

Old holes a reviewer files as such:

- A chain that commits less than an agent's `writes`: the planner's grant outside `only` still covers a builder change there (decision 1; no tree snapshot).
- An agent with `writes: None` that ran before the plan commit keeps its grant outside `only`.
- `only` entries are compared literally, so a planner with glob `writes` can't make a plan commit (P3 row, 755d88ad).
- `.justfile`, which `just` also reads: next to `justfile` it makes `just` refuse to run. A nuisance, not a bypass.
- The 8.3 short name of a file protected only by a glob entry (`adws/adw_*.py` is also under `adws/`, so no shipped entry).
- A filesystem that folds further than Unicode's canonical caseless match (compatibility forms, locale tables), and a tool that reads a file by a name no entry lists.
- A symlink at a permitted path that points at a protected one (v12), and `.gitattributes` in a permitted folder (v20).
- The other rows of 755d88ad's review: the `give_up` traceback (P2 Low), the local-mode rows (P3), the guard chain's `commit_all` without `run` (P3).
- Making `sandbox: openshell` the default.

Known false stops, none hit by a shipped roster or chain:

- An agent that runs again after an `only` commit can't commit inside that commit's entries until a full commit passes.
- The always-writable and `writes` matches stay exact: `writes` naming `justfile` can't write `JUSTFILE` (decision 2), and no `writes` entry unlocks `Justfile` or `Specs/`.
- A file whose name is shaped like an 8.3 alias (`notes~1`, `a~2.txt`) is protected wherever a protected entry has a component in that position: at the repo root, and directly under a folder that holds a protected entry.

## For the engineer

- `adws/`: run `just sssf-update` after the merge, so the hub's own chain gets both changes. `adws/adw_guard_change.py` needs no edit for this run: it revises the plan before its plan commit.
- The brief's example `JUSTFI~1` is refused, but NTFS never makes that alias; `CLAUDE~1` is the real case.
- A project whose recipe file is `Justfile` or whose folder is `Specs/` finds it protected after this, with no `writes` entry that unlocks it. No shipped roster or installer makes one.
