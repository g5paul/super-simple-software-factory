# Load only the main checkout's .env, and refuse an agent's commit of a secret file

Second try, after guard run 6f7eb831 was discarded. This is revision 2 of the spec, after spec review 1; the design is the same, and the Bar, Out of scope, the `just` command and the docs step changed. All paths are from the repo root. `S` below means `.claude/skills/sssf`.

## Baseline

`main` at `c7b8066`. Every finding is judged against that commit.

## What the baseline does (checked by the planner on 2026-11-25)

- `S/templates/adws/adw_modules/utils.py:35-36`: `DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))`, then `load_dotenv()`. python-dotenv's `find_dotenv()` (source read) takes the folder of the first calling frame outside `dotenv/main.py`, which is `utils.py`'s own folder, and returns the first `.env` at or above it (`_is_file_or_fifo`). So a worktree's copy finds, in order: the worktree's `adws/adw_modules/`, its `adws/`, its root, `adws/adw_data/worktrees/`, `adws/adw_data/`, `adws/`, then the main checkout.
- `S/templates/adws/adw_modules/worktree.py:93-100`: `main_checkout()` is `common_dir().parent`, from the process's working folder, with git's location variables removed. So on the baseline the `.env` is found from the file and the main checkout from the working folder: two starts.
- `S/templates/justfile:12`: `set dotenv-load`; line 19 reads `SSSF_CONFIG` with `env_var_or_default`.
- `permissions.judge_commits` (`permissions.py:1000`), `ref_moves` (`:2384`) and `enforce` (`:2577`) judge paths with `permitted()` only. Nothing looks at `secret_files`. `protected_files` names no `.env`. The existing secret-file rules cover reads only: the text rule (`secret_files.shell_rule`), and agentsh, which refuses any open of a file named `.env`, so a local agent under `local_guard: agentsh` can't write one with a plain open, but can still commit one with `git hash-object -w --stdin` and `git commit-tree`. A VM agent has neither. So the commit rule is all new.
- `judge_commits` passes a path found in `carried` without any agent's grant: the test is an `or` beside `permitted`/`taken` (`permissions.py:1035-1040`). `only` is tested before it.
- `just` 1.59.0, tested in a scratch folder: `set dotenv-command := "<cmd>"` runs `<cmd>` in the justfile's folder (also when `just` starts in a subfolder), loads its stdout as an env file before `env_var_or_default` is read, never replaces a key already in the environment, works in a file loaded with `mod`, and a non-zero exit stops `just` with the command's stderr and exit code. It can't be combined with `set dotenv-load` (parse error). `set dotenv-path` takes no backtick or variable ("const context"), so it can't name a looked-up path. `JUST_NO_DOTENV=true` turns all of it off; the agentsh wrap sets that (`agentsh_guard.py:1143`).
- uv 0.10.3, tested in a scratch folder holding a `.python-version` that names `./evil/python`, a `uv.toml`, and a `.venv` whose `bin/python` is a planted script: `uv run --no-project -q python …` runs the planted `./evil/python`. `uv run --no-project --no-config …` and `uv run --no-project --no-config --python '>=3.9' …` both run `/usr/bin/python3` and never the planted files. So `--no-project` alone, which the baseline's recipes use, lets a worktree file choose the interpreter; `--no-config` does not.

### Who starts in a worktree

After `worktree.enter()` moves the process (`worktree.py:282` resume, `:311` new):

- SSSF's own children run the main checkout's scripts by absolute path, so they import the main checkout's `utils`: the recheck after a rebase (`worktree.py:592-595`, `uv run <root>/adws/adw_quality.py`, `cwd` the worktree), a trial's chains (`trial.py:485-487`), the VM re-exec (`sandbox_remote.py:371`, `sys.argv[0]`).
- Quality blocks, `worktree_setup` commands and agents run in the worktree with the orchestrator's environment. None imports `utils` unless the command itself is `just` or `uv run adws/...`, which then use the worktree's justfile and `utils` copy.
- By hand: the engineer running `just <recipe>` or `uv run adws/adw_*.py` from a run's worktree, as the `just(...)` hints and `just rebase` conflicts invite. This is the P1 path.

The builder checks this list against the code (`grep -rn "subprocess\|execvp" S/templates/adws`) and corrects the spec copy's lines if any is off.

## Threat model

A VM agent under `host_agents: false` commits any file outside the protected paths; its commits land in the run's worktree on the host. A local agent runs as the engineer's user and can write any file the user can, including git objects and refs. Neither can change the environment of a host process that already runs.

What this guard can't stop at its level: a local agent can write an untracked `.env` anywhere (outside agentsh), plant a `.git` entry in a worktree's `adws/`, edit the worktree's justfile or `checkout.py` if its `writes` name them, or set up a filesystem alias of `.env`. The commit rule matches names, not content.

## Bar

Against `c7b8066`: no agent file kept, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise. And:

- **One `.env`.** Inside a git repo, no SSSF process and no template recipe loads a `.env` other than the main checkout's, in any layout. The one exception is on purpose: a copy of SSSF that sits in no git repo (no `.git` entry at or above `utils.py`) keeps the baseline's walk, as the request asks. There is no worktree there to hide a `.env` in.
- **No narrowing.** `agent_env` never holds back fewer names than on the baseline.
- **Stop, don't walk.** Inside a git repo where there is no main checkout to find, SSSF stops with a message naming the layout. It never falls back to the walk.
- **Secret commits.** No agent's commit that adds, changes or deletes a secret-named path passes `judge_commits`, `ref_moves` or the after-call check, on any ref. The one exception: in `judge_commits`, a path that holds exactly the mode and blob of `carried` (the tree that was already in the local checkout when the process started, `session.py:250-253`) is excused from the secret rule, and from nothing else. So a carried `.env.local` passes a full commit, and is refused outside `only`. That exception is an old hole this change keeps, listed under Out of scope.
- **No path passes a commit check on the head that the baseline refuses.**
- **The new `just` step runs no agent code.** The `dotenv-command` runs before every template recipe, including recipes that started no `uv` on the baseline. Its `uv` call takes no interpreter, config or environment from a file in the worktree: it carries `--no-project --no-config --python '>=3.9'`, and Python runs with `-I`. A test plants a `.python-version` naming a script, a `uv.toml` and a `.venv/bin/python` script in the worktree and shows none runs. What it does run is the worktree's `adws/adw_modules/checkout.py`, a protected path that only an agent whose `writes` name it can change, the same as the justfile that names it.

## The change

### 1. New `S/templates/adws/adw_modules/checkout.py` (standard library only)

One lookup, used by `utils.py`, `worktree.py` and the justfile. It can't live in `utils.py`, because the justfile must run it without python-dotenv, and `utils` can't import `worktree`.

- Move `GIT_LOCATION_VARS` and `git_location_env()` here from `utils.py`. `utils.py` re-imports both, so `utils.GIT_LOCATION_VARS` and `utils.git_location_env` keep working for every caller and test.
- `class NoMainCheckout(RuntimeError)`.
- `from_common(common: Path) -> Path`: what the common dir alone says. Reads the common dir's own config file, never the environment: `git config --file <common>/config --get core.worktree` and `--type=bool --get core.bare` (exit 1 is "unset"; any other failure raises `NoMainCheckout`). In order:
  - `core.worktree` set: that path, resolved from the common dir. This is a submodule (`<super>/.git/modules/<name>`).
  - `core.bare` true: raise. "This is a bare repo with linked worktrees (`<common>`). It has no main checkout, so SSSF has no `.env`, `repo.yaml` or trace folder to use. Clone it normally and run SSSF from the clone."
  - the common dir is a folder named `.git`: its parent. A plain clone, as today.
  - otherwise raise. "This is a linked worktree of a repo whose git dir (`<common>`) is outside its checkout (`git init --separate-git-dir`). Git doesn't record where that checkout is, so SSSF can't find the main checkout from here. Run SSSF from the main checkout, without `--worktree`."
- `main_checkout_from(start: Path) -> Path | None`:
  1. `top` = the first folder at or above `start.resolve()` holding a `.git` entry (plain path checks, as `worktree.checkout_folder()` does). None: return `None`, "not in a git repo". Walking for `.git` ourselves, instead of asking git from `start`, is what keeps a bare-repo shape (`HEAD`, `objects/`, `refs/`) in `adws/adw_modules/` from being read as a repo, with or without `safe.bareRepository=explicit`.
  2. From `top`, with `git_location_env()`: `git rev-parse --path-format=absolute --git-common-dir --git-dir`. A failure raises `NoMainCheckout` with git's first stderr line.
  3. If the git dir is the common dir (this is the main worktree), the config sets no `core.worktree`, and `core.bare` is not true: `git rev-parse --show-toplevel` from `top`. This is a plain clone's main checkout or a separate-git-dir repo's.
  4. Otherwise `from_common(common)`.
  Every returned path is `.resolve()`d. There is no `linked` flag.
- Script mode, `python -I checkout.py --dotenv`, for `just`: start from this file's folder. In a repo: write the bytes of `<main checkout>/.env` to stdout if it's a regular file, nothing if it's absent; `NoMainCheckout` or an unreadable file prints the message to stderr and exits 2. Outside a repo: print the first regular `.env` at or above the working folder, which is what `set dotenv-load` did.

The builder confirms each layout in a scratch repo before relying on it (the request's 2026-11-25 findings: a submodule has `core.worktree`; a separate git dir has nothing naming the checkout).

### 2. `S/templates/adws/adw_modules/utils.py`

Replace lines 35-36, inside the same `try` with the `except PermissionError` block and its `SSSF_AGENTSH` sign left exactly as they are:

- `main = main_checkout_from(Path(__file__).resolve().parent)`.
- `main is None` (no git repo): the two baseline lines, unchanged.
- Otherwise: names = the keys of `<main>/.env` (if a regular file) plus the keys of the file `find_dotenv()` returns, when it returns one. That second file is exactly the one the baseline would have loaded from this copy, so `DOTENV_KEYS` is always a superset of the baseline's. Set `DOTENV_KEYS`, then `load_dotenv(<main>/.env)` if it exists. Nothing else is loaded. The names are still read before any key is set.
- `NoMainCheckout` becomes `SystemExit("SSSF: <message>")`, so every process that imports `utils` stops with one line and no caller's `except Exception` swallows it.
- Rewrite the comment above to say which file loads and why the walked file only gives names.

Behaviour changes to document: an engineer's own linked worktree with its own `.env` and its own copy of SSSF now gets the main checkout's keys (its own file's names are still held back from agents); a repo with no `.env` no longer loads one from a folder above the repo.

### 3. `S/templates/adws/adw_modules/worktree.py`

- `main_checkout()` returns `checkout.main_checkout_from(Path.cwd())`. `None` and `NoMainCheckout` both raise `WorktreeError` (it raises outside a repo today; `agentsh_guard.py:477` relies on that). Update the docstring. `common_dir()` is unchanged.
- `enter()`: when `requested`, before `git worktree prune`, call `checkout.from_common(common_dir())` and turn `NoMainCheckout` into `WorktreeError`. A separate-git-dir repo then refuses `--worktree` at launch, before any branch or folder is made, instead of stopping inside the new worktree.

**The two starts.** `utils` starts the lookup from its own file and `main_checkout()` from the working folder, as both do on the baseline. They are one function, so from the same repo they give the same folder: that covers the main checkout and every linked worktree of it, a run's worktree included, which is every case the request's bar names. They differ only when the working folder is in a different repo from the SSSF copy that is running: a nested repo or submodule inside the checkout, launched with the outer repo's scripts, or the skill's own tests, which run the hub's modules inside temp repos. Giving both the working folder as the start would let the folder a command is started from choose the `.env`; giving both the file would make SSSF work on its own repo whatever folder it was started in, which breaks those tests and the baseline's meaning of "the repo I'm in". So the split stays, and is safe for the bar:

- the `.env` loaded is the main checkout's of the code that runs. No working folder moves it, so a nested repo an agent plants and a run started inside it can't make SSSF load that repo's `.env`;
- `DOTENV_KEYS` is a superset of the baseline's whatever the working folder is, since it doesn't read it;
- `main_checkout()` there is the nested repo's, as on the baseline, so SSSF reads that repo's `repo.yaml`. That is `config.md`'s existing "A nested repo or submodule" limit. It stays open, and is listed under Out of scope.

A test pins this: the hub-style copy of `utils` imported with the working folder in a nested repo that has its own `.env` loads the outer main checkout's keys and none of the nested one's.

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

`set dotenv-command := "uv run --no-project --no-config --python '>=3.9' -q python -I adws/adw_modules/checkout.py --dotenv"`

`just` runs it in the justfile's folder, so from a worktree it runs the worktree's `checkout.py`, which finds the main checkout and prints that `.env`. `--no-config` and the explicit `--python` are what keep the worktree's `uv.toml`, `.python-version` and `.venv` from choosing the interpreter (tested above with uv 0.10.3); `-I` keeps `PYTHON*` variables and the working folder off the import path. The recipes' own `uv run` lines are not changed by this plan and still read those files, as on the baseline (the P2 row).

`SSSF_CONFIG` and every other key a recipe reads keep coming from the main checkout's `.env`; a `.env` in a worktree is no longer read, so a `SSSF_CONFIG` set only there stops applying. A key already in the environment still wins. The builder confirms the working folder is right when the file is loaded as `mod sssf 'sssf.just'` and `just` starts in a subfolder, and checks every other file that stamps or mentions `set dotenv-load` (`grep -rn "dotenv-load" S docs`).

A `just` without the setting fails to parse the file ("unknown setting"), so every recipe fails, `sssf-update` included; the way out is a newer `just`, then `uv run S/scripts/install.py --update`. `install.py` checks no `just` version today, so none is added there. The version that added `dotenv-command` is under "For the engineer".

### 5. `S/templates/adws/adw_modules/permissions.py`

- Helper `_secret(path, patterns) -> bool`: the last path component passes `secret_files.is_default`, or the path matches a `repo.yaml` `secret_files` pattern the way `secret_files._matches(name, path, patterns)` reads it. Exact names only. Patterns are read from the main checkout's `repo.yaml`, the way `agents.py:673` gets them; if they can't be read, the default names still apply and the check refuses (`GitFailed`).
- `judge_commits`: a secret path is refused whatever its status (`A`, `M`, `D`, `T`; a rename is a `D` and an `A` under `--no-renames`), for every agent, before the other tests. Only `carried` (`_in_carried` true for that path) lifts it. Then `only`, `permitted`/`taken`, `session`, `wrote` and `carried` run exactly as on the baseline. `session` and `wrote` never lift the secret rule. So a carried `.env.local` outside `only` is refused for `only`, and no path passes that the baseline refuses.
- `ref_moves`: a new commit's path is also barred when `_secret` is true, whatever the agent's `writes`. Reword the report's first line to "protected paths or secret files".
- After-call check, the run's own branch: `undo_commits` lists the paths its commits changed (one read-only `git log --format= --name-only -z --no-renames <before>..<after>`, before the reset) and records the secret ones on the `before` dict. `enforce`, which already gets that dict as `head_before` (`agents.py:422-429` and `:582-584` call the two in that order), adds them to its breach: "`<agent>` committed secret file(s) `<paths>`; the commits were undone and the changes are in the tree, uncommitted." `enforce` raises, so the tree rollback still runs first. `enforce` gets no tree-level secret rule: an uncommitted secret file an agent wrote is caught when the chain commits it (`check_local_commit` calls `judge_commits`).

A repo that tracks a `.env` on purpose keeps it: the engineer's own commits aren't judged.

### 6. Who relied on a worktree `.env`

The builder greps `S/templates/repo.yaml`, `S/cookbooks`, `S/references` and `S/scripts/install.py` for a `worktree_setup` or step that writes `.env` into a worktree (the planner's grep found none) and records the answer in the spec copy. A repo whose own `worktree_setup` copies `.env` into the worktree loses nothing SSSF reads: SSSF and the template recipes now ignore that copy. The repo's own tools still read it.

### 7. Docs

`S/references/config.md`, limit by limit (line numbers at `c7b8066`):

| Where | What it says now | After this change | Rewrite |
|---|---|---|---|
| `:739` "a justfile with `set dotenv-load` then hands that recipe the real keys" | an agent's own recipe gets `.env`'s keys | **Still open.** The template's `dotenv-command` hands a recipe the main checkout's keys the same way, and so does a repo's own `set dotenv-load` | name both settings |
| `:739` "SSSF finds the main checkout without git's location variables… so a committed `.env` can't point the lookup at another repo" | true | **Changed.** Still true, and a worktree's `.env` is no longer loaded at all | add that, and the per-layout list: plain clone, linked worktree, submodule, separate git dir, bare |
| `:739` "If git can't answer inside a checkout… the launch stops" | true | **Changed.** It now stops when `utils` is imported, so every SSSF command stops, and the template's recipes stop too | add the stop table from step 3 |
| `:739` "**A nested repo or submodule**" | SSSF reads that repo's missing `repo.yaml` | **Closed for a submodule that has SSSF installed**: its own checkout is the main checkout. **Still open for a nested repo, or a submodule, entered with the outer repo's scripts**: SSSF reads the inner repo's `repo.yaml` and the outer main checkout's `.env` | rewrite the limit to the open half |
| `:739` "**A `.git` file**" | submodule or separate git dir: `host_agents` read as `true` | **Closed** for a submodule and for a separate-git-dir repo's main worktree. **Changed** for a linked worktree of a separate-git-dir repo and for a bare repo with worktrees: SSSF stops with a message | replace the limit with those two stops |
| `:741` "`.env` (through a justfile's `set dotenv-load`)" in the engineer's-own-steps list | a run's `.env` loads on `just rebase`, `just quality`, `just export`, `just merge` | **Closed** for the template justfile, which reads no worktree `.env`, and an agent's commit of one is refused. **Still open** for a repo's own justfile with `set dotenv-load`, when a `.env` got into the worktree uncommitted or through `carried`; and still open for `uv.toml`, `.python-version`, `.npmrc` and hooks | rewrite the sentence; add that the template's own lookup step ignores `uv.toml` and `.python-version` while the recipes' `uv run` lines don't |
| `:869` "The wrap sets `JUST_NO_DOTENV=true`, so `just` recipes under `set dotenv-load` don't load a `.env` the policy denies" | true | **Changed in wording only.** The variable also turns the template's `dotenv-command` off, so recipes under the wrap get no `.env` keys, as before | name `dotenv-command` |
| `:966` "A key counts as from `.env` when `.env` names it… so a `just` recipe's `set dotenv-load` changes nothing" and the `PermissionError` sentences | one `.env` | **Changed.** The names are the main checkout's `.env`'s plus those of the first `.env` at or above the SSSF copy that runs; only the first file's keys are loaded; either file unreadable stops SSSF, with the same agentsh exception. "A shell export… whose name `.env` doesn't set passes" is **still open** | rewrite the paragraph |

Also in `config.md`: what a repo's own `set dotenv-load` does in a worktree (loads the worktree's `.env`; SSSF still holds those names back when the file is above the SSSF copy that runs); the two behaviour changes in step 2; and, in "Secret files, `secret_files` and `agent_env`" (`:954`), that an agent's commit of a secret file is refused on any ref, with the carried exception, naming `.env.template`, `.env.test` and `.env.dist` as common names now refused (rename to `.env.example` or `.env.sample`, or commit it yourself).

Other docs:

- `S/cookbooks/install.md`: the `just` minimum and what an older `just` shows.
- `docs/open.md`: delete the P1 row "A chain that starts in a run's worktree loads the `.env`…". Cut the P2 row "An agent can commit `.env`, `uv.toml` or `.python-version`…" to what's left (`uv.toml`, `.python-version`, `adw_export`'s hooks), and add to it that `uv run --no-project` without `--no-config` runs an interpreter a committed `.python-version` names (shown above). Add rows: a `.git` file or folder planted in a worktree's `adws/` or `adws/adw_modules/` moves or stops the lookup (`git status` shows neither); a resume commits a secret file an interrupted agent wrote, through `carried` (`session.py:250-253`); a carried path passes `judge_commits` without any agent's grant and past `taken` (`permissions.py:1035-1040`); names that alias `.env` only on macOS or NTFS; the `just` minimum is unconfirmed until the engineer checks the changelog.
- `docs/designs/agentsh/corpus.sh`: add the case `ju''st test live`. `'just' test live` is already there (line 26, "quoted command word"); leave it.
- A CHANGELOG entry if the repo keeps one; comments in `S/templates/env.sample` if they describe loading.
- For the documenter: the `app_docs/` write-up for spec d0200ae6 describes `main_checkout()` as the folder above the common dir. Note that it's superseded; don't edit it.

## Tests

New files, built on the shipped `S/templates/sssf.config.yaml`:

`S/tests/test_main_env.py`. A fixture builds a temp repo holding copies of the template `adws/adw_modules/` and `justfile`, with a worktree at `adws/adw_data/worktrees/<id>`. A probe is a subprocess of `sys.executable` that imports that copy's `utils` and prints `DOTENV_KEYS`, chosen `os.environ` keys and `agent_env`'s result. The probe also computes the baseline's names itself (the first `.env` above the copy's folder) and asserts `DOTENV_KEYS` contains them: that's the no-narrowing test, run for every layout.

- committed `.env` at the worktree root with `SSSF_PROBE=worktree`: main checkout's keys, no `SSSF_PROBE`;
- empty `.env` in the worktree's `adws/`, and in the main checkout's `adws/adw_data/`: a main-checkout key is still held back;
- layouts: plain clone, linked worktree, submodule (`git -c protocol.file.allow=always submodule add`), separate git dir from its main worktree and from a linked one, bare repo (`git clone --bare`) with a linked worktree. Each asserts `main_checkout_from`, the file loaded, `DOTENV_KEYS`, and the stop message and exit status where the spec stops; `enter(requested=True)` refuses in the separate-git-dir repo;
- outside a git repo: the copy loads the first `.env` above it, as the baseline does;
- the two starts: working folder in a nested repo with its own `.env`, the outer copy of `utils` loads the outer main checkout's keys only;
- `GIT_DIR` and `GIT_COMMON_DIR` set to another repo: nothing moves;
- `HEAD`, `objects/`, `refs/` planted in the worktree's `adws/adw_modules/`, with and without `safe.bareRepository=explicit`: the import works and loads the main checkout's `.env`;
- `just` (skip when `shutil.which("just")` is None; drop `JUST_NO_DOTENV` from the child's environment): a recipe printing the environment, run from the worktree, from a submodule, and through `mod sssf`, shows the main checkout's key and not the worktree's; in the bare layout it exits 2 with the message;
- the pinned `uv` call: with a `.python-version` naming a planted script, a `uv.toml` and a planted `.venv/bin/python` in the worktree, the template's `dotenv-command` string, read out of the template and run in the worktree, prints the main checkout's `.env` and the planted scripts' marker file is never written.

agentsh refuses writing any file named `.env`, so every test that writes one is `host_only`, as `test_main_checkout_lookup.py` does. The layout tests of `main_checkout_from` write no `.env` and run under agentsh. No test pushes. Each docstring says what the baseline does in that case.

`S/tests/test_secret_commits.py`: `judge_commits` refuses commits that add `.env`, add `sub/.env.local`, rename a file to `.env`, delete a tracked `.env`, change a tracked `.env`; passes one adding `.env.example`; a carried `.env.local` passes without `only` and is refused outside `only`. `ref_moves` refuses `git update-ref refs/heads/other $(git commit-tree …)` on a commit adding `.env` plus a `src/` file, for an agent with `writes: ["src/"]`. `undo_commits` then `enforce` raise `PermissionBreach` for a local agent's own commit of `.env`, ignored-and-forced as well as plain. Commits are made with `git hash-object`/`commit-tree` where a `.env` file can't be written, so these run under agentsh.

## Verification

1. From `S`: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_main_env.py tests/test_secret_commits.py tests/test_main_checkout_lookup.py tests/test_worktree.py tests/test_secret_files.py tests/test_commit_check.py tests/test_agent_commits.py tests/test_operator_env.py -q -p no:cacheprovider -m 'not pushes and not host_only'` exits 0. Don't run the whole suite under agentsh.
2. The repo's own test command (whole suite, on the host) exits 0, the `host_only` cases included.
3. `bash -n docs/designs/agentsh/corpus.sh` exits 0.
4. Never open, copy or print the hub's own `.env`.

## Out of scope

Old holes this change leaves open. A reviewer files them as old holes, not as findings on this change.

- **Carried without a grant.** On `c7b8066` a path that matches `carried` passes `judge_commits` with no agent's `permitted` and past `taken` (`permissions.py:1035-1040`). The request says carried is never excused from `taken`; this plan does not change that baseline behaviour, and only makes sure the new secret rule adds no further excuse (`only` is still tested first). Filed as a `docs/open.md` row.
- **A resume commits a carried secret file** an interrupted agent wrote (`session.py:250-253`). Row filed.
- **A nested repo, or a submodule, entered with the outer repo's scripts**: `main_checkout()` is the inner repo, so its `repo.yaml` decides `host_agents`, while the `.env` is the outer main checkout's. `config.md:739`'s limit, kept and reworded.
- **A planted `.git` entry** in a worktree's `adws/` or `adws/adw_modules/` moves or stops the lookup. Row filed.
- **A repo's own justfile** with `set dotenv-load` still loads a worktree's `.env`; `config.md` says so.
- **The template recipes' own `uv run` lines** still read a worktree's `uv.toml` and `.python-version`, and `--no-project` alone runs an interpreter `.python-version` names. Only the new lookup step is pinned. The P2 row, with that added.
- **An agent's own recipe or script** still gets the main checkout's keys through the template justfile outside agentsh (`config.md:739`).
- The xattr gap that makes the main checkout's `.env` unreadable.
- Other files a VM run's commits put in front of host processes (`.npmrc`, hooks on export).
- Which names `secret_files` matches, and case or 8.3 aliases of them. Row filed.
- The text checks' gaps; the text guard is frozen.
- A FIFO named `.env` on the walk blocks the import, as it does on the baseline.
- An agent whose `writes` name the worktree's `justfile` or `adws/adw_modules/checkout.py` can change what `just` loads there, as it can change any recipe today.
- A folder literally named `.git` used as a separate git dir under an unrelated folder reads as a plain clone, as git itself reads it.

## For the engineer

- **The `just` minimum.** The planner had no network, so the version that added `set dotenv-command` is not established. Check `just`'s changelog, then correct the number in `S/cookbooks/install.md` and close the `docs/open.md` row. Until then the docs say "1.59.0, the version tested".
- **The hub's install.** `adws/`, `justfile` and `sssf.just` at the repo root are protected and outside the builder's `writes`. After the merge, update the hub's install and commit it, then run the proof in the request: the decoy `.env` from a worktree and from a scratch submodule, and one `--sandbox openshell` run told to commit a `.env`.
