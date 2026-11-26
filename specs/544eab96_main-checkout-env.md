# Load only the main checkout's .env, and refuse an agent's commit of a secret file

Second try, after guard run 6f7eb831 was discarded. All paths are from the repo root. `S` below means `.claude/skills/sssf`.

## Baseline

`main` at `c7b8066`. Every finding is judged against that commit.

## What the baseline does (checked by the planner on 2026-11-25)

- `S/templates/adws/adw_modules/utils.py:35-36`: `DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))`, then `load_dotenv()`. python-dotenv's `find_dotenv()` (source read) takes the folder of the first calling frame outside `dotenv/main.py`, which is `utils.py`'s own folder, and returns the first `.env` at or above it (`_is_file_or_fifo`). So a worktree's copy finds, in order: the worktree's `adws/adw_modules/`, its `adws/`, its root, `adws/adw_data/worktrees/`, `adws/adw_data/`, `adws/`, then the main checkout.
- `S/templates/adws/adw_modules/worktree.py:93-100`: `main_checkout()` is `common_dir().parent`, from the process's working folder, with git's location variables removed.
- `S/templates/justfile:12`: `set dotenv-load`; line 19 reads `SSSF_CONFIG` with `env_var_or_default`.
- `permissions.judge_commits` (`permissions.py:1000`), `ref_moves` (`:2384`) and `enforce` (`:2577`) judge paths with `permitted()` only. Nothing looks at `secret_files`. `protected_files` names no `.env`. The existing secret-file rules cover reads only: the text rule (`secret_files.shell_rule`), and agentsh, which refuses any open of a file named `.env`, so a local agent under `local_guard: agentsh` can't write one with a plain open, but can still commit one with `git hash-object -w --stdin` and `git commit-tree`. A VM agent has neither. So the commit rule is all new.
- `just` 1.59.0, tested in a scratch folder: `set dotenv-command := "<cmd>"` runs `<cmd>` in the justfile's folder (also when `just` starts in a subfolder), loads its stdout as an env file before `env_var_or_default` is read, never replaces a key already in the environment, works in a file loaded with `mod`, and a non-zero exit stops `just` with the command's stderr and exit code. It can't be combined with `set dotenv-load` (parse error). `set dotenv-path` takes no backtick or variable ("const context"), so it can't name a looked-up path. `JUST_NO_DOTENV=true` turns all of it off; the agentsh wrap sets that (`agentsh_guard.py:1143`).

### Who starts in a worktree

After `worktree.enter()` moves the process (`worktree.py:282` resume, `:311` new):

- SSSF's own children run the main checkout's scripts by absolute path, so they import the main checkout's `utils`: the recheck after a rebase (`worktree.py:592-595`, `uv run <root>/adws/adw_quality.py`, `cwd` the worktree), a trial's chains (`trial.py:485-487`), the VM re-exec (`sandbox_remote.py:371`, `sys.argv[0]`).
- Quality blocks, `worktree_setup` commands and agents run in the worktree with the orchestrator's environment. None imports `utils` unless the command itself is `just` or `uv run adws/...`, which then use the worktree's justfile and `utils` copy.
- By hand: the engineer running `just <recipe>` or `uv run adws/adw_*.py` from a run's worktree, as the `just(...)` hints and `just rebase` conflicts invite. This is the P1 path.

The builder checks this list against the code (`grep -n "subprocess\|execvp" S/templates/adws -r`) and corrects the spec copy's lines if any is off.

## Threat model

A VM agent under `host_agents: false` commits any file outside the protected paths; its commits land in the run's worktree on the host. A local agent runs as the engineer's user and can write any file the user can, including git objects and refs. Neither can change the environment of a host process that already runs.

What this guard can't stop at its level: a local agent can write an untracked `.env` anywhere (outside agentsh), plant a `.git` entry in a worktree's `adws/`, edit the worktree's justfile or `checkout.py` if its `writes` name them, or set up a filesystem alias of `.env`. The commit rule matches names, not content.

## Bar

Against `c7b8066`: no agent file kept, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise. And:

- No SSSF process and no template recipe loads a `.env` other than the main checkout's, in any layout.
- `agent_env` never holds back fewer names than on the baseline.
- Where there is no main checkout to find, SSSF stops with a message naming the layout. It never falls back to the walk inside a git repo.
- No agent's commit that adds, changes or deletes a secret-named path lands, on any ref.
- No path passes a commit check on the head that the baseline refuses.

## The change

### 1. New `S/templates/adws/adw_modules/checkout.py` (standard library only)

One lookup, used by `utils.py`, `worktree.py` and the justfile. It can't live in `utils.py`, because the justfile must run it without python-dotenv, and `utils` can't import `worktree`.

- Move `GIT_LOCATION_VARS` and `git_location_env()` here from `utils.py`. `utils.py` re-imports both, so `utils.GIT_LOCATION_VARS` and `utils.git_location_env` keep working for every caller and test.
- `class NoMainCheckout(RuntimeError)`.
- `main_checkout_from(start: Path, *, linked: bool = False) -> Path | None`:
  1. `top` = the first folder at or above `start.resolve()` holding a `.git` entry (plain path checks, as `worktree.checkout_folder()` does). None: return `None`, "not in a git repo". Walking for `.git` ourselves, instead of asking git from `start`, is what keeps a bare-repo shape (`HEAD`, `objects/`, `refs/`) in `adws/adw_modules/` from being read as a repo.
  2. From `top`, with `git_location_env()`: `git rev-parse --path-format=absolute --git-common-dir --git-dir`. A failure raises `NoMainCheckout` with git's first stderr line.
  3. Read the common dir's own config file, never the environment: `git config --file <common>/config --get core.worktree` and `--type=bool --get core.bare` (exit 1 is "unset"; any other failure raises `NoMainCheckout`).
  4. Decide, in this order:
     - `core.worktree` set: that path, resolved from the common dir. This is a submodule (`<super>/.git/modules/<name>`), from its checkout or a linked worktree of it.
     - `core.bare` true: raise. "This is a bare repo with linked worktrees (`<common>`). It has no main checkout, so SSSF has no `.env`, `repo.yaml` or trace folder to use. Clone it normally and run SSSF from the clone."
     - the git dir is the common dir and `linked` is false: `git rev-parse --show-toplevel` from `top`. A plain clone's main checkout, or a separate-git-dir repo's.
     - the common dir is a folder named `.git`: its parent. A linked worktree of a plain clone, as today.
     - otherwise raise. "This is a linked worktree of a repo whose git dir (`<common>`) is outside its checkout (`git init --separate-git-dir`). Git doesn't record where that checkout is, so SSSF can't find the main checkout from here. Run SSSF from the main checkout, without `--worktree`."
  Every returned path is `.resolve()`d.
- Script mode, `python -I checkout.py --dotenv`, for `just`: start from this file's folder. In a repo: write the bytes of `<main checkout>/.env` to stdout if it's a regular file, nothing if it's absent; `NoMainCheckout` or an unreadable file prints the message to stderr and exits 2. Outside a repo: print the first regular `.env` at or above the working folder, which is what `set dotenv-load` did.

The builder confirms each layout in a scratch repo before relying on it (the request's 2026-11-25 findings: submodule has `core.worktree`; separate-git-dir has nothing naming the checkout).

### 2. `S/templates/adws/adw_modules/utils.py`

Replace lines 35-36, inside the same `try` with the `except PermissionError` block and its `SSSF_AGENTSH` sign left exactly as they are:

- `main = main_checkout_from(Path(__file__).resolve().parent)`.
- `main is None` (no git repo): the two baseline lines, unchanged.
- Otherwise: names = the keys of `<main>/.env` (if a regular file) plus the keys of the file `find_dotenv()` returns, when it returns one. That second file is exactly the one the baseline would have loaded from this copy, so `DOTENV_KEYS` is always a superset of the baseline's. Set `DOTENV_KEYS`, then `load_dotenv(<main>/.env)` if it exists. Nothing else is loaded. The names are still read before any key is set.
- `NoMainCheckout` becomes `SystemExit("SSSF: <message>")`, so every process that imports `utils` stops with one line and no caller's `except Exception` swallows it.
- Rewrite the comment above to say which file loads and why the walked file only gives names.

Behaviour changes to document: an engineer's own linked worktree with its own `.env` now gets the main checkout's keys (its own file's names are still held back from agents); a repo with no `.env` no longer loads one from a folder above the repo.

### 3. `S/templates/adws/adw_modules/worktree.py`

- `main_checkout()` returns `checkout.main_checkout_from(Path.cwd())`. `None` and `NoMainCheckout` both raise `WorktreeError` (it raises outside a repo today; `agentsh_guard.py:477` relies on that). It stays based on the working folder, as today; `utils` starts from its own file, as the baseline's walk does. Update the docstring. `common_dir()` is unchanged.
- `enter()`: when `requested`, before `git worktree prune`, call `main_checkout_from(root, linked=True)` and turn `NoMainCheckout` into `WorktreeError`. A separate-git-dir repo then refuses `--worktree` at launch, before any branch or folder is made, instead of stopping inside the new worktree.

Where the stop reaches, for the spec and `config.md`:

| Command | bare repo + worktrees | separate git dir, main worktree | separate git dir, linked worktree | Baseline there |
|---|---|---|---|---|
| a run (`adw_*.py`) | stops at import | runs; `--worktree` refused at launch | stops at import | ran; `main_checkout()` was the git dir's parent, so `repo.yaml`, `host_agents`, caps and trace were read from the wrong folder; `.env` was whatever the walk found |
| template `just` recipes | every recipe stops (exit 2) | load the checkout's `.env` | every recipe stops | loaded the first `.env` above the justfile |
| `scripts/install.py` | unaffected (imports neither `utils` nor `worktree`; builder confirms) | same | same | same |
| dashboard (`apps/visualizer`) | unaffected when started directly; `just obs` stops with the recipes | runs | as bare | ran |

A submodule works everywhere and now reads the submodule's own checkout; the baseline read `<super>/.git/modules`.

### 4. `S/templates/justfile`

Replace `set dotenv-load` (line 12) and its comment with:

`set dotenv-command := "uv run --no-project -q python -I adws/adw_modules/checkout.py --dotenv"`

`just` runs it in the justfile's folder, so from a worktree it runs the worktree's `checkout.py`, which finds the main checkout and prints that `.env`. `-I` keeps `PYTHON*` variables and the working folder off the import path. `SSSF_CONFIG` and every other key a recipe reads keep coming from the main checkout's `.env`; a `.env` in a worktree is no longer read, so a `SSSF_CONFIG` set only there stops applying. A key already in the environment still wins. The builder confirms the working folder is right when the file is loaded as `mod sssf 'sssf.just'` and `just` starts in a subfolder, and checks every other file that stamps or mentions `set dotenv-load` (`grep -rn "dotenv-load" S docs`).

A `just` without the setting fails to parse the file ("unknown setting"), so every recipe fails, `sssf-update` included; the way out is a newer `just`, then `uv run S/scripts/install.py --update`. `install.py` checks no `just` version today, so none is added there. The version that added `dotenv-command` is under "For the engineer".

### 5. `S/templates/adws/adw_modules/permissions.py`

- Helper `_secret(path, patterns) -> bool`: the last path component passes `secret_files.is_default`, or the path matches a `repo.yaml` `secret_files` pattern the way `secret_files._matches(name, path, patterns)` reads it. Exact names only. Patterns are read from the main checkout's `repo.yaml`, the way `agents.py:673` gets them; if they can't be read, the default names still apply and the check refuses (`GitFailed`).
- `judge_commits`: a secret path is refused whatever its status (`A`, `M`, `D`, `T`; a rename is a `D` and an `A` under `--no-renames`), for every agent, before the other tests. Only `carried` (`_in_carried` true for that path) lifts it. Then `only`, `permitted`/`taken`, `session`, `wrote` and `carried` run exactly as on the baseline. `session` and `wrote` never lift the secret rule. So a carried `.env.local` outside `only` is refused for `only`, and no path passes that the baseline refuses. (The request says carried is never excused from `taken`. On the baseline a carried path already passes without an agent's grant; this plan leaves that as it is and adds nothing to it.)
- `ref_moves`: a new commit's path is also barred when `_secret` is true, whatever the agent's `writes`. Reword the report's first line to "protected paths or secret files".
- After-call check, the run's own branch: `undo_commits` lists the paths its commits changed (one read-only `git log --format= --name-only -z --no-renames <before>..<after>`, before the reset) and records the secret ones on the `before` dict. `enforce`, which already gets that dict as `head_before`, adds them to its breach: "`<agent>` committed secret file(s) `<paths>`; the commits were undone and the changes are in the tree, uncommitted." `enforce` raises, so the tree rollback still runs first. `enforce` gets no tree-level secret rule: an uncommitted secret file an agent wrote is caught when the chain commits it (`check_local_commit` calls `judge_commits`).

A repo that tracks a `.env` on purpose keeps it: the engineer's own commits aren't judged.

### 6. Who relied on a worktree `.env`

The builder greps `S/templates/repo.yaml`, `S/cookbooks`, `S/references` and `S/scripts/install.py` for a `worktree_setup` or step that writes `.env` into a worktree (the planner's grep found none) and records the answer in the spec copy. A repo whose own `worktree_setup` copies `.env` into the worktree loses nothing SSSF reads: SSSF and the template recipes now ignore that copy. The repo's own tools still read it.

### 7. Docs

- `S/references/config.md`: in "Keeping every agent in the VM, `host_agents`" (line 737) and "Secret files, `secret_files` and `agent_env`" (line 954): which `.env` loads per layout, the stop table above, what the template `just` loads, what a repo's own `set dotenv-load` does in a worktree (loads the worktree's `.env`; SSSF still holds those names back), the two behaviour changes in step 2, and that an agent's commit of a secret file is refused on any ref, naming `.env.template`, `.env.test` and `.env.dist` as common names now refused (rename to `.env.example`/`.env.sample`, or commit it yourself).
- `S/cookbooks/install.md`: the `just` minimum and what an older `just` shows.
- `docs/open.md`: delete the P1 row "A chain that starts in a run's worktree loads the `.env`…". Cut the P2 row "An agent can commit `.env`, `uv.toml` or `.python-version`…" to what's left (`uv.toml`, `.python-version`, `adw_export`'s hooks). Add rows: a `.git` file or folder planted in a worktree's `adws/` or `adws/adw_modules/` moves or stops the lookup (`git status` shows neither); a resume commits a secret file an interrupted agent wrote, through `carried` (`session.py:250-253`); names that alias `.env` only on macOS or NTFS; the `just` minimum is unconfirmed until the engineer checks the changelog.
- `docs/designs/agentsh/corpus.sh`: add the case `ju''st test live`. `'just' test live` is already there (line 26, "quoted command word"); leave it.
- A CHANGELOG entry if the repo keeps one; comments in `S/templates/env.sample` if they describe loading.
- For the documenter: the `app_docs/` write-up for spec d0200ae6 describes `main_checkout()` as the folder above the common dir. Note that it's superseded; don't edit it.

## Tests

New files, built on the shipped `S/templates/sssf.config.yaml`:

`S/tests/test_main_env.py`. A fixture builds a temp repo holding copies of the template `adws/adw_modules/` and `justfile`, with a worktree at `adws/adw_data/worktrees/<id>`. A probe is a subprocess of `sys.executable` that imports that copy's `utils` and prints `DOTENV_KEYS`, chosen `os.environ` keys and `agent_env`'s result. The probe also computes the baseline's names itself (the first `.env` above the copy's folder) and asserts `DOTENV_KEYS` contains them: that's the no-narrowing test, run for every layout.

- committed `.env` at the worktree root with `SSSF_PROBE=worktree`: main checkout's keys, no `SSSF_PROBE`;
- empty `.env` in the worktree's `adws/`, and in the main checkout's `adws/adw_data/`: a main-checkout key is still held back;
- layouts: plain clone, linked worktree, submodule (`git -c protocol.file.allow=always submodule add`), separate-git-dir from its main worktree and from a linked one, bare repo (`git clone --bare`) with a linked worktree. Each asserts `main_checkout_from`, the file loaded, `DOTENV_KEYS`, and the stop message and exit status where the spec stops; `enter(requested=True)` refuses in the separate-git-dir repo;
- `GIT_DIR` and `GIT_COMMON_DIR` set to another repo: nothing moves;
- `HEAD`, `objects/`, `refs/` planted in the worktree's `adws/adw_modules/`, with and without `safe.bareRepository=explicit`: the import works and loads the main checkout's `.env`;
- `just` (skip when `shutil.which("just")` is None; drop `JUST_NO_DOTENV` from the child's environment): a recipe printing the environment, run from the worktree, from a submodule, and through `mod sssf`, shows the main checkout's key and not the worktree's; in the bare layout it exits 2 with the message.

agentsh refuses writing any file named `.env`, so every test that writes one is `host_only`, as `test_main_checkout_lookup.py` does. The layout tests of `main_checkout_from` write no `.env` and run under agentsh. No test pushes. Each docstring says what the baseline does in that case.

`S/tests/test_secret_commits.py`: `judge_commits` refuses commits that add `.env`, add `sub/.env.local`, rename a file to `.env`, delete a tracked `.env`, change a tracked `.env`; passes one adding `.env.example`; a carried `.env.local` passes without `only` and is refused outside `only`. `ref_moves` refuses `git update-ref refs/heads/other $(git commit-tree …)` on a commit adding `.env` plus a `src/` file, for an agent with `writes: ["src/"]`. `undo_commits` then `enforce` raise `PermissionBreach` for a local agent's own commit of `.env`, ignored-and-forced as well as plain. Commits are made with `git hash-object`/`commit-tree` where a `.env` file can't be written, so these run under agentsh.

## Verification

1. From `S`: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_main_env.py tests/test_secret_commits.py tests/test_main_checkout_lookup.py tests/test_worktree.py tests/test_secret_files.py tests/test_commit_check.py tests/test_agent_commits.py tests/test_operator_env.py -q -p no:cacheprovider -m 'not pushes and not host_only'` exits 0. Don't run the whole suite under agentsh.
2. The repo's own test command (whole suite, on the host) exits 0, the `host_only` cases included.
3. `bash -n docs/designs/agentsh/corpus.sh` exits 0.
4. Never open, copy or print the hub's own `.env`.

## Out of scope

- The xattr gap that makes the main checkout's `.env` unreadable.
- A repo's own justfile and its `.env` loading, past the `config.md` note.
- Other files a VM run's commits put in front of host processes (`uv.toml`, `.python-version`, hooks on export).
- Which names `secret_files` matches, and case or 8.3 aliases of them.
- The text checks' gaps; the text guard is frozen.
- A planted `.git` entry, the `carried` resume, and a FIFO named `.env` on the walk (it blocks the baseline too): open rows.
- An agent whose `writes` name the worktree's `justfile` or `adws/adw_modules/checkout.py` can change what `just` loads there, as it can change any recipe today.
- A folder literally named `.git` used as a separate git dir under an unrelated folder reads as a plain clone, as git itself reads it.

## For the engineer

- **The `just` minimum.** The planner had no network, so the version that added `set dotenv-command` is not established. Check `just`'s changelog, then correct the number in `S/cookbooks/install.md` and close the `docs/open.md` row. Until then the docs say "1.59.0, the version tested".
- **The hub's install.** `adws/`, `justfile` and `sssf.just` at the repo root are protected and outside the builder's `writes`. After the merge, update the hub's install and commit it, then run the proof in the request: the decoy `.env` from a worktree and from a scratch submodule, and one `--sandbox openshell` run told to commit a `.env`.
