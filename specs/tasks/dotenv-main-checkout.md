# Load only the main checkout's .env, and refuse an agent's commit of one

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `utils.py`, `worktree.py`, the `justfile` template, and `permissions.py`, which is guard code, so it goes through the chain.

The engineer decided on 2026-11-25: one `.env`, the main checkout's, for every SSSF process wherever it starts (option A), and the host refuses an agent's commit that adds or changes a secret file (option B). Both, in one run.

This is the second try. Guard run 6f7eb831 passed the chain and was discarded on 2026-11-25, after the bypass review found that it took the folder above git's common dir for the main checkout. "Where it went wrong" says what to do differently.

## What broke

`docs/open.md` has the P1 row ("A chain that starts in a run's worktree loads the `.env` the run's branch committed"). What the code at `main` shows, checked on 2026-11-25:

- **The walk.** `utils.py` loads `.env` at import: `DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))`, then `load_dotenv()`. `find_dotenv()` with no arguments starts from the calling file's folder (`adws/adw_modules/` of whichever copy was imported) and walks up to the first `.env`. For a process that imports a worktree's `utils.py` (`adws/adw_data/worktrees/<adw_id>/adws/adw_modules/`), the walk passes the worktree's `adws/`, the worktree's root, `adws/adw_data/worktrees/`, `adws/adw_data/` and `adws/` before it reaches the main checkout's `.env`. Check this against python-dotenv's source.
- **Who starts there.** The orchestrator imports `utils` in the main checkout, then moves into the worktree (`worktree.py:311`, `os.chdir(path)`; a resume at `:282`). List in the spec, with source lines, every process SSSF starts afterwards that imports `adw_modules.utils` or runs `just` with the worktree as its working folder or its `utils` copy, and what the engineer starts by hand in a worktree, such as `just` from there.
- **What a found `.env` does.** A `.env` the run's branch committed (a VM agent under `host_agents: false` can add one) sets its keys in that process and its children: `LD_PRELOAD`, `BASH_ENV`, `PYTHONPATH`, `NODE_OPTIONS`, `GIT_CONFIG_GLOBAL` or `GIT_DIR` would run agent files on the host. An empty `.env` found first gives an empty `DOTENV_KEYS`, so `agent_env` holds back nothing and every key already in the environment reaches agents. `adws/adw_data/` is gitignored and sits above every worktree, so a `.env` there hides the real one from every worktree process. Not reproduced.
- **`just`.** The `justfile` template has `set dotenv-load` (line 12), which loads the first `.env` in the justfile's folder or a folder above it before any recipe line runs. In a worktree that's the branch's, or an empty one in `adws/adw_data/`. `config` reads `SSSF_CONFIG` through `env_var_or_default` (line 19), so a key in `.env` can choose the roster today. A repo that loads `sssf.just` with `mod sssf` has its own justfile and its own settings.
- **`main_checkout()` has a layout bug of its own.** `worktree.main_checkout()` (line 93) returns `common_dir().parent`, with git's location variables removed (spec d0200ae6). That's the main checkout only when the common dir is `<checkout>/.git`. Checked on 2026-11-25 with git 2.x in scratch repos:
  - a plain clone, or a linked worktree of one: the common dir is `<main>/.git`, so its parent is right;
  - a submodule: the common dir is `<super>/.git/modules/<name>`, and its config's `core.worktree` names the checkout;
  - `git init --separate-git-dir`: the common dir is the separate folder, nothing in it names the checkout, and `git worktree list --porcelain` names the git dir as the main worktree;
  - a bare repo with linked worktrees: there's no main checkout.

  `host_agents`, the spend caps, the trace and worktree placement all read `main_checkout()`, so in the last three layouts they read the wrong folder today.
- **What exists to build on.** `permissions.judge_commits` judges every commit of a VM run before the host lands it (`sandbox_remote.py`, the `bundle unbundle` and `--ff-only` merge), and refuses the whole bundle on any refused path. `permissions.ref_moves` (line 2384) judges commits on other refs a local agent moved, by `permitted()` only. `secret_files.py` names the repo's secret files: `.env` and `.env.*` except `.env.sample` and `.env.example`, plus `repo.yaml`'s `secret_files`.

## Where 6f7eb831 went wrong

Its bypass review found these on its head. The fix has to meet each one.

1. **The wrong `.env` in three layouts (bypass, Medium).** It loaded `parent(--git-common-dir)/.env`. In a submodule, a separate-git-dir repo and a bare repo with worktrees that's not the checkout's file. `DOTENV_KEYS` came out empty, so `agent_env` passed agents a key that `main` holds back, whenever the repo's own justfile (`set dotenv-load` with `mod sssf`), direnv or the engineer's shell had put `.env` in the environment. It was shown end to end in a scratch submodule. The same happens to an engineer who launches from their own linked worktree that has its own `.env`.
2. **No `.env` at all in those layouts (harm, Medium).** Its `utils.py` and its template justfile both loaded nothing there, so runs lost `SSSF_*`, the Claude token and provider keys.
3. **Commits on other refs (old hole, Medium).** An agent with `writes: ["src/"]` that runs `git update-ref refs/heads/main $(git commit-tree ...)` on a commit adding `.env` passes `ref_moves` on `main` and on its head. That is the path to `main` the threat model names.
4. **A carried secret path (Low, latent).** Its `judge_commits` let a path found in `carried` skip the `only` and `taken` checks as well as the secret rule. No caller reached it.
5. **A bare-repo shape stopped every import (Low).** `HEAD`, `objects/` and `refs/` in `adws/adw_modules` made its `git rev-parse` fail under `safe.bareRepository=explicit`, and the import raised.
6. **The `just` version.** `set dotenv-command` makes a `just` without the setting fail to parse the whole template, and it didn't say which version added it.

## The change

1. **One lookup for the main checkout.** `main_checkout()` and the `.env` load use one function, in `utils.py` if `utils` can't import `worktree`, so the two can't disagree. Per layout:
   - a plain clone and its linked worktrees: `<common dir>/..`, as today;
   - a submodule: the common dir's `core.worktree`, resolved from the common dir;
   - a separate-git-dir repo: the process's own checkout when its git dir is the common dir (so it's the main worktree); from a linked worktree, there's no way to find it, so say what SSSF does;
   - a bare repo with linked worktrees: no main checkout.

   Where no main checkout can be found, SSSF stops with a message that names the layout, unless the spec shows that a narrower answer is safe. It never falls back to a walk that can reach a worktree's `.env`. Say which commands the stop reaches (a run, `just` recipes, `install.py`, the dashboard) and what each did there before. Keep git's location variables out of the lookup, as `main_checkout()` does now.
2. **One `.env`, and no fewer names held back.** `utils.py` loads keys from the main checkout's `.env` only. `DOTENV_KEYS` holds the names in that file and in every other `.env` `main`'s walk would have found from this copy of `utils.py`, so `agent_env` never holds back fewer names than on `main`. A `.env` anywhere else (a worktree's root, its `adws/`, `adws/adw_data/`) is never loaded. Outside a git repo, keep today's behaviour. Keep the `PermissionError` handling and its agentsh-only sign exactly as they are.
3. **`just` loads no worktree `.env`.** A recipe from the `justfile` template, run in a run's worktree, starts no process with a key from the worktree's `.env`, and loads the main checkout's in every layout item 1 handles. Say how from `just` 1.59.0's documentation or source. If it uses a setting older `just` lacks, find the version that added it in `just`'s changelog, say what a project with an older `just` sees, and put the minimum in `cookbooks/install.md` and in whatever tool check `install.py` already makes. Say what changes for a repo whose `.env` sets `SSSF_CONFIG` or other keys the recipes read, and keep that working from the main checkout. A repo's own justfile is the repo's; `config.md` says what its `set dotenv-load` does in a worktree.
4. **No agent commits a secret file, on any ref.** `permissions.judge_commits`, `ref_moves` and the after-call check refuse a commit that adds, changes, renames to, or deletes a path whose name `secret_files` matches, anywhere in the tree, for every agent, on the run's branch and on every other ref an agent moves. A path in `carried` is excused from the secret rule only, never from `only` or `taken`. Say whether `protected_files` or the existing secret-file rules already cover any of this, and add only what's missing. A repo that tracks a `.env` on purpose keeps it; only agents can't change it, and `config.md` says so, naming the common names that are now refused (`.env.template`, `.env.test`, `.env.dist`).
5. **Who relied on a worktree `.env`.** Say whether anything SSSF ships writes a `.env` into a worktree (`worktree_setup` examples, `install.py`, the docs), and what a repo that does it in its own `worktree_setup` loses.
6. **Docs.** Delete the `docs/open.md` P1 row named above, or cut it to what's left. `config.md`'s `host_agents` section and its `.env` notes say which `.env` SSSF loads in each layout, what `just` loads, where SSSF stops, and that an agent's commit of a secret file is refused on any ref. File as new `docs/open.md` rows, unless the change closes them: a `.git` file or folder an agent plants in a worktree's `adws/` or `adws/adw_modules/` that moves the lookup (`git status` shows neither); a resume committing a secret file an interrupted agent wrote, through `carried` (`session.py:250-253`); and names that alias `.env` only on macOS or NTFS. Add the two `just` spellings the review found past the text checks (`'just' test live`, `ju''st test live`) as cases in `docs/designs/agentsh/corpus.sh`.

## The bar

- **The committed `.env`.** In a temp repo with a worktree like a run's: a `.env` at the worktree's root setting `SSSF_PROBE=worktree`, a process that imports the worktree's `utils.py` from the worktree gets the main checkout's keys and no `SSSF_PROBE`, and its `DOTENV_KEYS` include the main checkout's names. On `main` it gets `SSSF_PROBE=worktree`.
- **The empty `.env`.** The same with an empty `.env` in the worktree's `adws/`, and again in the main checkout's `adws/adw_data/`: `agent_env` still holds back a key the main checkout's `.env` names. On `main` it holds back nothing.
- **Every layout.** For a plain clone, a linked worktree, a submodule, a separate-git-dir repo (from its main worktree and from a linked one) and a bare repo with a linked worktree, each with a `.env` holding a made-up key that's also in the environment: `main_checkout()`, the `.env` loaded and `DOTENV_KEYS` are what the spec says, `agent_env` holds back at least the names `main` holds back, and where the spec stops, it stops with its message. On `main`, the submodule case passes the key through `main_checkout()`'s wrong folder, or say what it does.
- **No narrowing.** For each layout, a test that `agent_env` on the head holds back every name it holds back on `main`.
- **`GIT_DIR` in the environment.** With `GIT_DIR` or `GIT_COMMON_DIR` set to another repo, the main checkout and the `.env` loaded don't move.
- **A bare-repo shape.** `HEAD`, `objects/` and `refs/` planted in a worktree's `adws/adw_modules/`: say what happens, and it's never a `.env` other than the main checkout's.
- **`just`.** In the same temp repo with the template's `justfile`, a recipe that prints the environment, run with `just` from the worktree, shows no key from the worktree's `.env` and shows the main checkout's. The same from a submodule. Mark it to skip where `just` isn't installed. On `main` it shows the worktree's key.
- **The commit.** `judge_commits` refuses a VM-style commit that adds `.env`, one that adds `sub/.env.local`, one that renames a file to `.env`, one that deletes a tracked `.env`, and one that changes a tracked `.env`; it passes one that adds `.env.example`. `ref_moves` refuses an agent's `update-ref` of another branch to a commit that adds `.env`, for an agent whose `writes` would otherwise allow the commit's other paths. A local agent's commit of `.env` is refused by the after-call check. A carried `.env.local` outside `only` is still refused for being outside `only`.
- **Nothing else changes.** Every existing test passes on the host, `test_secret_files.py`, `test_worktree.py`, `test_main_checkout_lookup.py` and the `utils` import tests among them.

## After the merge, the engineer's proof

Update the hub's install and commit it. In a scratch repo with SSSF installed and a decoy `.env` holding `SSSF_DECOY=main`: start a worktree run, put `SSSF_DECOY=worktree` in a `.env` at its root by hand, and run one SSSF command from the worktree and one `just` recipe from there. Both see `main`, never `worktree`. Then the same from a scratch submodule with SSSF installed. Then run one VM run (`--sandbox openshell`) whose agent is told to commit a `.env`, and see the bundle refused.

## Running the tests

The planner and the builder run under agentsh. Run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Every `.env` a test writes is in a temp repo and holds made-up keys. Never open, copy or print the hub's own `.env`; check it by key name only, if at all. A submodule test needs `-c protocol.file.allow=always` for `git submodule add` from a local path. Seed a test's bare repo with `git clone --bare` or `git fetch`. agentsh refuses every `git push` from an agent's processes, and one refused push stops the run, as it stopped guard run c98e111d's build on 2026-11-25. A test that must push gets the `pushes` marker.

## Out of scope

- The xattr gap that makes the main checkout's `.env` unreadable (its own row).
- A repo's own justfile and its own `.env` loading, past what `config.md` says.
- Other host processes a VM run's commits can reach, past `.env`: each is its own row.
- Changing which names `secret_files` matches.
- The text checks' own gaps. The text guard is frozen.
