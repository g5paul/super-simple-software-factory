# Let tools read their config above the workspace, and give agents their own caches

Item 5 of the Next list in `docs/designs/agentsh-local-sandbox.md`. The evidence and the engineer's decisions are in its section "Tool config and caches, 2026-11-09". Don't reopen them.

This is the rerun of run 45742573. Its spec, `specs/45742573_agentsh-tool-config-caches_v2.md` on commit `7ce5f4f`, passed review. This plan keeps that design and rebases it on `f6372f6`. The guard module, `data_types.py`, `deny-policy.py`, `config.md` and `test_agentsh_guard.py` are unchanged between that spec's baseline (`e1b2bb7`) and `f6372f6`. One correction: `test_absolute_links_work_as_on_main` can't stay as it is. Its first assertion links to the engineer's uv-managed Python, which this change makes READ only (see Tests §7).

Revision 3. It answers spec review 2 (`spec_review_2.md`):
- The workaround for a Python outside `/usr/bin` now sets `UV_PYTHON_BIN_DIR` to the agent cache's `bin`, passes `--no-bin`, and uses absolute paths. Without that, `uv python install` puts a `python3.x` link the agent can edit into `~/.local/bin`, on the engineer's PATH. The spec now says the installed Python is agent-writable and who may use it. For the engineer gains a check that `~/.local/bin` gained nothing. See Out of scope, the `config.md` limits, and For the engineer.

Revision 2 answered spec review 1 (`spec_review_1.md`):
- The folders above the workspace no longer get all of READ. A new rule, `sssf-config-folders`, allows only `open`, `stat`, `readlink` and `access` on them. `list` and `read` are left out (§1, "What opening a folder needs").
- §2 no longer says uv downloads a Python into the agent cache. The network rules refuse that download. A repo whose `requires-python` excludes `/usr/bin`'s Python can't make a fresh venv inside the wrap. This is named under Out of scope and in `config.md`'s limits, with a way around it.
- Out of scope names SSSF's new `mkdir` of the agent cache, which follows a link in place of `adws/adw_data` on a plain run.
- Not blocking: bun's `$XDG_CONFIG_HOME/.bunfig.toml` is recorded; Tests §4 says to set `agent_cache` in the `machine` fixture; the engineer's checks gain `cargo --version`.

## Baseline

`main` at `f6372f6` ("Record tool-config run 45742573 and test-fixture run 6e904be8"). Every finding is judged against it. On it, `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` has:
- `CONFIG_SEARCH` (line 86): `uv.toml` and `pyproject.toml` at `/`, `/home` and `${HOME}`;
- `TOOL_WRITE` (line 101): ALL on `~/.cache/uv`, `~/.local/share/uv`, `~/.npm/**`, `~/.cargo/registry/**` and `~/.cargo/git/**`;
- `sssf-system-read`: READ only, over `SYSTEM_READ`.

The installed build has the namespace fix and the ninth fork patch. So a link's target is resolved and judged with the `symlink` operation at the file it reaches, and `/proc` targets fail closed.

## Threat model

The agent runs as the engineer's own Unix user, inside `agentsh wrap`, under a default-deny file policy. It can:
- run any program the exec rules allow;
- set any environment variable for its own children;
- pass any flag, such as `uv --cache-dir ~/.cache/uv`;
- write any file the policy allows;
- put a link anywhere it may write, the main checkout's `adws/adw_data` included on a plain run.

The environment variables this change sets only change where tools go by default. The policy is what keeps an agent out of the engineer's caches. The kernel still applies the user's own permissions under every rule, so a rule that allows an operation on a root-owned folder gives the agent nothing the kernel refuses.

This guard can't stop these at its level:
- what an agent does inside the files it may write. That includes the workspace's `.venv`, which quality checks run outside the wrap by design;
- a tool outside the wrap that follows a link an agent made, where the link was moved later. That is the ninth patch's own limit;
- the content of a file the agent may read reaching the model's context. For the config files above the workspace, see the limits in §1.

## Bar

The change must:
1. Let a lookup of every name on the config list, in every folder above the workspace, pass the file rules for READ, on a plain run and on a `--worktree` run. The folder itself must pass for `open`, `stat`, `readlink` and `access`.
2. Under `local_guard: agentsh`, point every tool's cache and install folders at `<data_root>/agent-cache/`, the same for both harnesses. The policy then allows writes there, and no longer allows them on the engineer's caches.
3. Let `uv venv` on the system Python make `bin/python -> /usr/bin/python3.12` in the workspace.

It must never, against the baseline:
- allow a read of a file above the workspace that isn't on the config list, or any write above the workspace outside the agent cache folder;
- allow `list` on a folder above the workspace that the baseline didn't let an agent list;
- let a wrapped agent write a file the engineer's own tools later run outside the wrap: a cache, a tool folder, `~/.local/bin`, or a link in a system folder. That includes any step the docs tell the engineer to run;
- let one repo's agent write another repo's agent cache;
- keep an agent file, run agent code on the host, or delete or overwrite an engineer file where the baseline does otherwise.

## Out of scope

These holes stay open. A reviewer files them as old holes, or as stated costs of the engineer's decision:
- Relative link targets. The ninth fork patch covers them.
- `ENOENT` for a denied file that doesn't exist. That would need a fork change, so any name not on the list still answers `EACCES`.
- pi's own policy, the network rules, sudo, and turning agentsh on in another repo.
- **A fresh venv needs a Python in `/usr/bin`.** This is a cost of moving `UV_PYTHON_INSTALL_DIR`, which the engineer's decision of 2026-11-09 requires. Inside the wrap, uv no longer finds the engineer's managed Pythons, which live in `~/.local/share/uv/python` (3.10 and 3.13 on this machine). It can't download one either, because uv's Python downloads come from GitHub (python-build-standalone) and `sssf-registries` doesn't allow that host. So in a repo whose `requires-python` excludes `/usr/bin/python3.12`, these fail under the wrap: every fresh `uv venv`, a `uv sync` that has to create `.venv`, `uv run --with` and `uvx`. On the baseline they used the managed Python. These still work:
  - a `.venv` that already exists, on a uv-managed Python. The agent reads that Python through `HOME_READ`;
  - a worktree's `.venv` made by `worktree_setup`, which runs outside the wrap with the engineer's own environment;
  - a Python the engineer installs into the repo's agent cache, outside the wrap, from the repo's main checkout:

    ```sh
    F="$(git rev-parse --show-toplevel)/adws/adw_data/agent-cache"
    UV_PYTHON_INSTALL_DIR="$F/uv-python" UV_PYTHON_BIN_DIR="$F/bin" uv python install --no-bin 3.13
    ```

    Both paths are absolute. `--no-bin` stops uv from writing versioned `python3.x` executables at all. Since uv 0.8 it writes them by default, into `UV_PYTHON_BIN_DIR`, which is `~/.local/bin` on the engineer's PATH unless set. `UV_PYTHON_BIN_DIR="$F/bin"`, the value `cache_env` uses, is a second guard. If either is ignored, the links still land in `F/bin`, off the PATH. If a uv doesn't know `--no-bin`, it refuses before it installs anything. The Python this puts in `F/uv-python` is agent-writable. Only venvs made inside the wrap may use it, and so may the workspace venvs that quality checks run, by design. The engineer must never put `F/bin` on PATH, point their own uv at `F/uv-python`, or base a venv outside the wrap on it.

  Changing the network rules to allow the download is out of scope.
- **SSSF's `mkdir` of the agent cache follows links.** `start()` runs `Path(agent_cache).mkdir(parents=True, exist_ok=True)` outside the wrap. On a plain run, `adws/adw_data` is in the workspace, so an agent can swap it, or a folder above it, for a link. The `mkdir` then follows the link, and an empty `agent-cache` folder can appear at the target. No file is written or deleted there. `config.md` already has a limit of this kind: "SSSF makes the session folder's subfolders by path, so a folder link an agent put there is followed, and an empty folder can appear at its target". The same swap already moves where SSSF writes its session files on a plain run.
- Some ancestor lookups stay denied on purpose, because the tools that make them go on past `EACCES`:
  - cargo's `.cargo/config.toml` and `.cargo/config` in parent folders (the cargo book, `reference/config.md`, "Hierarchical structure");
  - npm's `node_modules` check in its prefix walk (`folders.md`, cited below);
  - Node's `node_modules` resolution walk (`doc/api/modules.md`, "Loading from node_modules folders");
  - bun's `$XDG_CONFIG_HOME/.bunfig.toml`. `XDG_CONFIG_HOME` is unset on this machine, and SSSF doesn't set it. Where an engineer sets it, bun reads that path too, gets `EACCES` unless the path is already readable, and goes on, as the harvest shows it does for `package.json`;
  - bun's listing of the folders above the workspace (`list`). bun skips a folder it can't read and goes on.

  yarn's `.yarnrc.yml` walk isn't listed either, because yarn wasn't run here. A tool found later to stop on one of these is a new request.
- **One repo's runs share one agent cache.** Run A's agent can edit a wheel in `F/uv`. Run B's `uv sync` then hardlinks it into B's `.venv`, which B's quality checks run outside the wrap. This is the baseline's hole, made narrower. On `f6372f6` the shared cache was `~/.cache/uv`, which every repo and the engineer's own tools use. Now it reaches only wrapped agents' venvs in the same repo.
- On a plain run, the workspace is the main checkout, so the agent can already write its `.venv`, which the engineer uses later. That is the baseline's, unchanged.
- pip's `~/.cache/pip`, which was never writable. pip warns and goes on.
- Global installs stay denied, as on the baseline: `npm i -g`, `PNPM_HOME`, `BUN_INSTALL_GLOBAL_DIR` and `bun add -g`.
- A file an agent wrote into an engineer cache before this change.
- The design's `docs/designs/agentsh/sssf-dd-claude.yaml` and `sssf-dd-pi.yaml` are the 2026-11-08 default-deny test's policies, rendered for sssf-sandbox. They aren't regenerated.

## Design

### 1. The config list above the workspace

**Folders above the workspace.** These come from two values that `file_rules` already has:
- `main = Path(git_dir).parent`, the main checkout;
- `data_root = Path(sessions).parent`, which `guard_for` resolves to `<main>/adws/adw_data` by default.

The folders are the union of:
- every strict parent of `main` (`Path(main).parents`), for a plain run;
- `<data_root>/worktrees` and every parent of it, for a `--worktree` run. That run's workspace is `<data_root>/worktrees/<adw_id>` (`worktree.path_for`, `worktree.py:65`).

Sort them shallowest first (by `len(Path(d).parts)`, then by the string), with no duplicates. `main` itself is on the list only through the worktree chain. On a plain run it is the workspace anyway.

**Names, with sources.** This is the record of what tools read, and each name is cited:

| Name | Where | Tool and source |
|---|---|---|
| `uv.toml`, `pyproject.toml` | every folder above | uv. Docs: `docs/concepts/configuration-files.md` ("uv will search for a `pyproject.toml` or `uv.toml` file in the current directory, or in the nearest parent directory"). Source: `crates/uv-settings/src/lib.rs`, where `FilesystemOptions::find` walks `ancestors()`, and `crates/uv-workspace/src/workspace.rs`, which walks the ancestors for a workspace root's `pyproject.toml`. Harvest of 2026-11-09: read in every folder up to `/` |
| `.python-version`, `.python-versions` | every folder above | uv. Docs: `docs/concepts/python-versions.md`, which looks for a `.python-version` file in the current directory or any of its parents. Source: `crates/uv-python/src/version_files.rs`: `PYTHON_VERSION_FILENAME`, `PYTHON_VERSIONS_FILENAME`, and `discover`, which walks the ancestors |
| `package.json` | every folder above | Corepack: its README says it uses the closest `package.json`, and `sources/specUtils.ts` `loadSpec` walks up. bun: `docs/install/workspaces.md`, where the workspace root is found by walking up `package.json`. npm: `docs/content/configuring-npm/folders.md` ("walk up the folder tree checking for a folder that contains either a package.json file, or a node_modules folder"), which ships in npm's own package. Node: `doc/api/packages.md`, "nearest parent package.json". Harvest: Corepack stops on it, bun skips it |
| `pnpm-workspace.yaml` | every folder above | pnpm: `@pnpm/find-workspace-dir` runs `find-up` for `pnpm-workspace.yaml`. Docs: `pnpm-workspace_yaml.md` |
| `.bunfig.toml` | `${HOME}` only | bun's global config. Docs: `docs/runtime/bunfig.md` ("a global configuration file at `$HOME/.bunfig.toml` or `$XDG_CONFIG_HOME/.bunfig.toml`"). Harvest: read at `~/.bunfig.toml`. `XDG_CONFIG_HOME` is unset here (see Out of scope). bun's local `bunfig.toml` sits in the project root, which is the workspace |

The builder copies this table into the module as a comment beside the constant, sources included.

uv's user and system config (`~/.config/uv/uv.toml`, `/etc/uv/uv.toml`) are already readable, through `HOME_READ` and `SYSTEM_READ`.

**What opening a folder needs.** `FOLDER_OPS = ["open", "stat", "readlink", "access"]`, on each folder's own exact path. Not `list` and not `read`. From the tools' source:
- uv walks the ancestors with `Path::ancestors()` and checks each with `Path::is_dir()`, `fs::metadata` and `fs::canonicalize` (`crates/uv-fs`, `crates/uv-workspace/src/workspace.rs`). Those are `stat`, `access`, and the `readlink` that glibc's `realpath` makes on each part. It then opens the file by path. It never reads a folder's entries.
- bun opens each folder (`open` with `O_DIRECTORY`) and opens `package.json` relative to it. Its resolver also lists each folder's entries, and on `EACCES` it treats the folder as empty and goes on (Out of scope).
- Corepack calls `fs.existsSync` and `fs.readFileSync` on `<folder>/package.json` (`sources/specUtils.ts`). It makes no folder operation of its own.

The harvest events, in `~/.local/share/agentsh-harvest/cfg1/`, can't be read from inside the wrap: `ls` answered `Permission denied` for this planner on 2026-11-10. So these operations come from source. The hub session confirms them against the events before the install (For the engineer). If uv or Corepack needs another operation on a folder above the workspace, that's a spec change, not a builder fix.

`/`, `/home`, `$HOME` and `<main>` already get all of READ through `sssf-system-read`, `HOME_READ` and `sssf-main-config`. So `list` there is the baseline's. The folders this change opens are `$HOME/github`, `<main>/adws`, `<main>/adws/adw_data` and `<main>/adws/adw_data/worktrees`, and none of them may be listed. An agent learns only whether a name it already guesses exists there, and its metadata.

**Code** (`.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`):
- `CONFIG_NAMES = ("uv.toml", "pyproject.toml", ".python-version", ".python-versions", "package.json", "pnpm-workspace.yaml")`, `HOME_CONFIG = ["${HOME}/.bunfig.toml"]` and `FOLDER_OPS = ["open", "stat", "readlink", "access"]`. Put the source table in a comment above `CONFIG_NAMES`, and the folder-ops reasons above `FOLDER_OPS`. Keep the existing comment on why a denied lookup breaks uv (`EACCES`, not `ENOENT`).
- `def folders_above(main: str, data_root: str) -> list[str]`, as specified above.
- `def config_search(main: str, data_root: str) -> list[str]`. For each folder `d` from `folders_above`, it adds `f"{d.rstrip('/')}/{n}"` for each name, so `/` gives `/uv.toml`. Then it adds `HOME_CONFIG`. Delete the old `CONFIG_SEARCH`.
- In `file_rules`, set `data_root = str(Path(sessions).parent)`. In the place of the old `sssf-config-search`, put:
  - `rule("sssf-config-search", config_search(main, data_root), READ, "allow")`;
  - straight after it, `rule("sssf-config-folders", folders_above(main, data_root), FOLDER_OPS, "allow")`.

**Rendered paths for the hub.** Here `main = $HOME/github/super-simple-software-factory` and `data_root = <main>/adws/adw_data`. The folders, shallowest first, are `/`, `/home`, `$HOME`, `$HOME/github`, `<main>`, `<main>/adws`, `<main>/adws/adw_data` and `<main>/adws/adw_data/worktrees`.
- `sssf-config-folders` allows `open`, `stat`, `readlink` and `access` on exactly those 8 folders, in that order.
- `sssf-config-search` allows READ on exactly 49 paths, in this order. First, for each of the 8 folders, `<folder>/uv.toml`, `<folder>/pyproject.toml`, `<folder>/.python-version`, `<folder>/.python-versions`, `<folder>/package.json` and `<folder>/pnpm-workspace.yaml`, which makes 48. Then the literal `${HOME}/.bunfig.toml`, last.

`$HOME` is written as the concrete home folder everywhere but the last entry. A plain run looks up only the first 4 folders. A worktree run looks up all 8.

**For a repo at `$HOME/github/<repo>`:** the same 8 folders and the same 49 files, with `<main> = $HOME/github/<repo>`.

These stay denied, among others:
- `$HOME/github/<sibling>/pyproject.toml`, and the folder `$HOME/github/<sibling>` itself;
- `$HOME/github/notes.txt`, `$HOME/github/uv.toml.bak` and `$HOME/github/uv.toml/x`;
- `list` and `read` on `$HOME/github`, `<main>/adws`, `<main>/adws/adw_data` and `<main>/adws/adw_data/worktrees`;
- `<main>/README.md` on a worktree run;
- `<main>/adws/adw_data/worktrees/<other-id>/pyproject.toml`;
- `$HOME/github/<other>/adws/adw_data/agent-cache/**`;
- every write operation on every listed path.

**Limits, for `config.md`.** An agent can now read the whole of each of these files:
- a `[[tool.uv.index]]` URL in `uv.toml` or `pyproject.toml` can carry a token (`https://user:token@host/simple`);
- `~/.bunfig.toml` can hold `install.registry` with a token;
- `package.json` can hold anything;
- on a worktree run, the main checkout's own `pyproject.toml`, `package.json` and the rest, uncommitted edits included.

### 2. The agents' own caches

**The folder.** `F = <data_root>/agent-cache`, beside `sessions`. Each repo has its own data root, and no other repo's policy allows this one. On a plain run the folder is inside the workspace. On a worktree run a new rule allows it.

**Code:**
- `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`: add `agent_cache: str = ""` to `LocalGuard`, with a one-line comment ("the agents' own tool caches, under agentsh").
- `agentsh_guard.py`:
  - In `file_rules`, add `rule("sssf-agent-cache", [cache, f"{cache}/**"], ALL, "allow")` straight after `sssf-sessions`, where `cache = str(Path(sessions).parent / "agent-cache")`. No new parameter is needed. Every deny rule stays ahead of it, so `.env` and secret files inside `F` stay denied.
  - Delete `TOOL_WRITE` and the `sssf-tool-write` rule.
  - Add `"/.local/share/uv/python"`, `"/.local/share/uv/python/**"`, `"/.local/share/uv/tools"` and `"/.local/share/uv/tools/**"` to `HOME_READ`, which is READ only.
  - In `guard_for`, build `LocalGuard(mode="agentsh", policies=..., config_template=template, agent_cache=str(Path(sessions).parent / "agent-cache"))`.
  - Add `def cache_env(folder: str) -> dict[str, str]`, which returns the table below, with a docstring.
  - In `start()`, after the policy lookup and before `child.update(...)` of the agentsh keys:
    1. If `guard.agent_cache` is empty, raise `AgentshRefused` ("no agent cache folder reached the launch; this is a bug in the caller").
    2. Run `Path(guard.agent_cache).mkdir(mode=0o700, parents=True, exist_ok=True)`. This runs in SSSF, outside the wrap. Out of scope covers how it follows links.
    3. Drop every key in `child` whose `.lower()` equals the `.lower()` of a `cache_env` key, so an engineer's `NPM_CONFIG_CACHE` can't win over ours.
    4. Run `child.update(cache_env(guard.agent_cache))`.

    Both harnesses go through `start()` (`agent_cc.py:306`, `agent_pi.py:703`), so they get the same variables. Text mode and VM trees return earlier in `start()` and get none.

**What agents may read of the engineer's.** Only the uv-managed Pythons and uv tools, READ only. An existing `.venv` in the main checkout links to `~/.local/share/uv/python/...`, and `~/.local/bin/ruff` links into `~/.local/share/uv/tools/`. Without this read, both break on a plain run. It's safe because:
- READ has no write, delete, rename, chmod or `symlink`, so an agent can't change those files or put a file there;
- `symlink` isn't in READ, so no new link may point there either;
- inside the wrap each operation on those programs is still judged.

Nothing else of the engineer's caches stays readable. Once the variables move, no tool needs them, so the smallest allow is none:
- `~/.local/share/uv` itself, and its other children, stay denied, because `uv auth` keeps credentials in `~/.local/share/uv/credentials/`;
- `~/.cache/uv`, `~/.npm`, `~/.cargo/registry` and `~/.cargo/git` are no longer readable at all.

**The variables** (`F = agent_cache`). Each row cites the docs page:

| Variable | Value | Default it moves | What moving it changes |
|---|---|---|---|
| `UV_CACHE_DIR` | `F/uv` | `~/.cache/uv` | Wheels, sdists, git checkouts, and the `uvx` and `--with` environments move to `F/uv`. A first `uv sync` in a repo downloads again, and the workspace venv hardlinks from `F/uv`. Docs: `docs/reference/environment.md`, `docs/concepts/cache.md` |
| `UV_PYTHON_INSTALL_DIR` | `F/uv-python` | `~/.local/share/uv/python` | uv no longer finds the engineer's managed Pythons when it makes a new environment. It uses a system Python in `/usr/bin` of the right version (§3), or a Python the engineer put in `F/uv-python` outside the wrap with the command in Out of scope. It can't download one, because the network rules refuse uv's download host. So a repo whose `requires-python` excludes `/usr/bin`'s Python can't make a fresh venv under the wrap (Out of scope). Existing venvs keep working through the read above |
| `UV_PYTHON_BIN_DIR` | `F/bin` | `~/.local/bin` | `uv python install` puts its `python3.x` links in `F/bin`, off the engineer's PATH |
| `UV_TOOL_DIR` | `F/uv-tools` | `~/.local/share/uv/tools` | `uv tool install` builds its tool environments in `F` |
| `UV_TOOL_BIN_DIR` | `F/bin` | `~/.local/bin` | Tool executables go to `F/bin`, off the engineer's PATH. On the baseline a write to `~/.local/bin` was denied |
| `npm_config_cache` | `F/npm` | `~/.npm` | npm's cache, `_npx` and `_logs` move to `F/npm`, so `mkdir ~/.npm` is no longer needed. An environment setting wins over a project `.npmrc` (npm `docs/content/using-npm/config.md`, the precedence list). `~/.npmrc` stays denied |
| `BUN_INSTALL_CACHE_DIR` | `F/bun` | `~/.bun/install/cache` | bun's package cache moves. Docs: `docs/install/cache.md` |
| `BUN_RUNTIME_TRANSPILER_CACHE_PATH` | `F/bun-transpiler` | inside bun's cache | The transpiler cache moves. Docs: `docs/runtime/env.md` |
| `COREPACK_HOME` | `F/corepack` | `~/.cache/node/corepack` | Corepack downloads pnpm and yarn into `F`. Corepack README, "Environment variables" |
| `npm_config_store_dir` | `F/pnpm-store` | `~/.local/share/pnpm/store` | pnpm's content store moves. pnpm reads `npm_config_*` settings (pnpm docs `npmrc.md`, `store-dir`) |
| `npm_config_cache_dir` | `F/pnpm-cache` | `~/.cache/pnpm` | pnpm's metadata cache (`cache-dir`) moves. npm may print `Unknown env config` on stderr for this variable and for `store-dir`. npm still exits 0 |
| `CARGO_HOME` | `F/cargo` | `~/.cargo` | `registry/`, `git/`, `.package-cache` and the `bin/` that `cargo install` writes all move to `F/cargo`. cargo has no separate variable for the registry or git folders (the cargo book, `reference/environment-variables.md` and `guide/cargo-home.md`). cargo then reads `F/cargo/config.toml`, which is absent, instead of `~/.cargo/config.toml`. No config is lost: on the baseline agents couldn't read `~/.cargo/config.toml` anyway, since `HOME_READ` has only `.cargo`, `.cargo/env` and `.cargo/bin/**`. cargo still reads the `.cargo/config.toml` files in the workspace, and there is no `credentials.toml` in `F`. `~/.cargo/bin/cargo`, the rustup proxy, still runs through `HOME_READ`. The toolchains stay READ at `RUSTUP_HOME`, `~/.rustup`. The engineer checks `cargo --version` under the service after the install |

**Why this meets the harm bar.** After the change, a wrapped agent may write no path that the engineer's own tools read by default. Only processes that carry these variables read `F`, and only `start()` sets them. Quality checks don't go through `start()`. The engineer's uv, npm, bun and cargo, run outside the wrap, never look there. The one documented command that the engineer runs against `F`, the `uv python install` in Out of scope, writes only into `F`, and writes nothing to `~/.local/bin`. A venv that links into `F` sits in a workspace the agent writes, and quality checks run that workspace by design. The sharing of `F` across one repo's runs is listed under Out of scope as a narrowed old hole.

**Git.** `F` is inside the main checkout, so on a plain run `git add -A` would commit it. Add this line to `GITIGNORE_ENTRIES` in `.claude/skills/sssf/scripts/install.py`, after the `adws/adw_data/worktrees/` entry at line 66:

`"adws/adw_data/agent-cache/",  # agents' own tool caches under local_guard: agentsh`

Add the same entry to the hub's own `.gitignore`, on the line after `adws/adw_data/worktrees/` (line 153).

### 3. A link to a system file

Add `SYSTEM_LINK = ["/usr/bin/**", "/bin/**"]`, and add `rule("sssf-system-link", SYSTEM_LINK, ["symlink"], "allow")` directly after `sssf-system-read`. Put a comment above `SYSTEM_LINK` with the reason it is safe, from below.

**Why only these.** Bar item 3 needs one link. `uv venv` makes `bin/python` as an absolute link to the base interpreter, `/usr/bin/python3.12` here. Its other links (`python3 -> python`, `python3.12 -> python`) are relative and inside the workspace, so the ninth patch already judges them there. Nothing else in the request needs a link into a system folder.
- `/usr/bin/**` covers the resolved target. `readlink -f /usr/bin/python3` is `/usr/bin/python3.12` here.
- `/bin/**` covers a target written `/bin/python3` on a system where `/bin` is a real folder. With usrmerge, `/bin` is a link to `usr/bin`, and the tracer resolves it to `/usr/bin/...`.

The link's own path still needs a rule that allows `symlink` there: the workspace, temp, sessions, or `F`.

**Why no agent can make a link inside these paths.** The rule also allows `symlink` where these paths are the link's own path, so the kernel has to refuse it there. Both trees belong to the package manager:
- `/usr/bin` is `root:root 755`, and flat: it has no subfolders.
- `/bin` is a root-owned link to `usr/bin`. On a system without usrmerge, it is a root-owned `755` folder.

Checked on this machine on 2026-11-10:
- `find /usr/bin /bin/ -type d -writable`, without `-xdev`, printed nothing;
- `findmnt` shows no mount under `/usr/bin` or `/bin`. The mounts under `/usr` are all in `/usr/lib` and `/usr/src`.

**Left out:** `/usr/lib/**`, `/usr/local/**`, `/opt/**`, `/mnt/**`, `/etc/**`, `/proc/**`, `/sys/**`, and the bare folders. In particular:
- `/opt`: it is empty here, `root 755`. Elsewhere it often holds folders the user owns, so allowing `symlink` there could let an agent plant a link in a tool's install.
- `/mnt`: only `/mnt` itself is in `sssf-system-read`, but `/mnt/c` is `gpaul 777` and `/mnt/d` is `root 777` here, so `/mnt/**` must never be in this rule.
- `/usr/lib`: `/usr/lib/node_modules` is often chowned to the user, and `/usr/lib/wsl/lib` is a read-write overlay mount.

A Python outside `/usr/bin`, for example under `/usr/local` or `/opt`, can't be a venv's base. That fails as it does on the baseline.

## Files to change

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: §1, §2 and §3.
- `.claude/skills/sssf/templates/adws/adw_modules/data_types.py`: `LocalGuard.agent_cache`.
- `docs/designs/agentsh/deny-policy.py`: mirror the file rules exactly. `test_file_rules_equal_the_designs_deny_policy_output` compares the two, with `sessions="/m/proj/adws/adw_data/sessions"`. Port `CONFIG_NAMES`, `HOME_CONFIG`, `FOLDER_OPS`, `folders_above`, `config_search`, `SYSTEM_LINK`, the `HOME_READ` additions, `sssf-config-folders`, `sssf-agent-cache` and `sssf-system-link`. Remove `CONFIG_SEARCH` and `TOOL_WRITE`. Its `main()` already passes `f"{main}/adws/adw_data/sessions"`, which matches the test.
- `.claude/skills/sssf/scripts/install.py`: the gitignore entry.
- `.gitignore`: the same entry.
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: the tests below.
- `.claude/skills/sssf/references/config.md`: the docs below.

Don't touch `adws/adw_modules/`, the hub's installed copy, which is protected. See For the engineer.

## Tests

All tests go in `.claude/skills/sssf/tests/test_agentsh_guard.py`. They use the shipped `sssf.config.yaml` (`SHIPPED`, `both_harnesses()`) where a config is needed, and the existing `Rules` emulation and `glob_regex`.

1. **The rendered lists.** Run `agentsh_guard.policy_text(RepoSettings(), f"{main}/.git", h, "n", sessions=f"{main}/adws/adw_data/sessions", refuse_script=f"{main}/adws/adw_modules/sssf_refuse.py")` for `main` in `/home/u/github/super-simple-software-factory` and `/home/u/github/repo`, and for both harnesses.
   - The paths of `sssf-config-folders` equal the 8 folders in §1, in order, and its operations equal `["open", "stat", "readlink", "access"]`.
   - The paths of `sssf-config-search` equal the 49 entries in §1, in order, with `/home/u` for `$HOME`. The last is the literal `${HOME}/.bunfig.toml`. Its operations equal `READ`.
   - Test `folders_above` directly with a data root outside `main` (`/data/x`). The result holds the parents of `main` and of `/data/x/worktrees`, shallowest first, with no duplicates.
2. **Lookups pass, and the rest is denied.** Use `Rules(text, "/home/u", root)` twice: once with `root = main` (a plain run), and once with `root = f"{main}/adws/adw_data/worktrees/abc"` (a worktree run). Assert only the folders strictly above `root`. On the plain run the worktree chain's folders below `main` are inside the workspace.
   - For every such folder and every name in `CONFIG_NAMES`, `first_match(f"{folder}/{name}", op)` is `sssf-config-search` for each op in `READ`.
   - For every such folder, each op in `FOLDER_OPS` decides allow.
   - `list` and `read` decide `deny` on `/home/u/github`, and on the worktree run also on `<main>/adws`, `<main>/adws/adw_data` and `<main>/adws/adw_data/worktrees`. (`list` on `/`, `/home`, `/home/u` and `<main>` is the baseline's allow and isn't asserted.)
   - `first_match("/home/u/.bunfig.toml", "read") == "sssf-config-search"`.
   - Each "stays denied" path in §1 decides `deny` for `read`.
   - Every listed file and folder decides `deny` for `write`, `create`, `delete` and `symlink`.
3. **Caches.**
   - On the worktree root, writes to `<main>/adws/adw_data/agent-cache/uv/x` and `.../agent-cache/cargo/registry/x` match `sssf-agent-cache`, and a read of `.../agent-cache/x/.env` matches `sssf-secret-files`.
   - `write`, `create` and `mkdir` decide `deny` for each of these: `~/.cache/uv/x`, `~/.cache/uv`, `~/.local/share/uv/python/x`, `~/.local/share/uv/tools/x`, `~/.local/share/uv/x`, `~/.npm/x`, `~/.npm`, `~/.cargo/registry/x`, `~/.cargo/git/x`, `~/.local/bin/x`, `~/.bun/install/cache/x`, `~/.cache/node/corepack/x` and `/home/u/github/other/adws/adw_data/agent-cache/x`.
   - `read` is allowed for `~/.local/share/uv/python/cpython-3.12/bin/python3.12` and `~/.local/share/uv/tools/ruff/bin/ruff`.
   - `read` is denied for `~/.cache/uv/x`, `~/.npm/x` and `~/.local/share/uv/credentials/credentials.toml`.
4. **The environment.** Set `agent_cache=str(tmp_path / "agent-cache")` on the `LocalGuard` the `machine` fixture builds (`test_agentsh_guard.py:184`). It is the only agentsh `LocalGuard` the tests build, and without it every launch test refuses. Then add a sibling of `test_each_harness_gets_its_policy_the_env_and_a_null_stdin`, with the same `machine` and `launch` fixtures:
   - every `cache_env` key is in the child's env, under `guard.agent_cache`, and the values are the same for both harnesses;
   - `UV_PYTHON_BIN_DIR` and `UV_TOOL_BIN_DIR` both equal `f"{guard.agent_cache}/bin"`;
   - an incoming `UV_CACHE_DIR=/x`, `NPM_CONFIG_CACHE=/y` or `CARGO_HOME=/z` doesn't survive, in any case spelling;
   - the folder exists after `start`, with mode `0o700`;
   - an empty `agent_cache` (`machine.guard.model_copy(update={"agent_cache": ""})`) refuses with `AgentshRefused`, and no process starts.

   `test_text_mode_starts_exactly_as_the_tree_does` already proves text mode passes the env through unchanged. Leave it as it is, and it must pass. Next to `test_read_puts_the_sessions_folder_in_the_main_checkout`, check that `read()` sets `agent_cache` to `<main>/adws/adw_data/agent-cache`, from the main checkout and from inside a worktree.
5. **Links.** Use the `world` fixture.
   - `r.link(f"{R}/.v/bin/python", "/usr/bin/python3.12")` and `r.link(f"{R}/.v/bin/python", "/bin/python3")` both return `"allow"`.
   - With an allowed target such as `/usr/bin/python3.12`, a link path at any of these decides `deny`: `/usr/lib/x`, `/usr/local/bin/x`, `/opt/x`, `/opt/a/x`, `/mnt/c/x`, `/mnt/x`, `/etc/x`, `/x`, `/home/x` and `/var/x`.
   - A target at any of these decides `deny`: `/usr/lib/x86_64-linux-gnu/libc.so.6`, `/usr/local/bin/python3`, `/opt/py/bin/python3`, `/mnt/c/x`, `/etc/passwd` and `/proc/self/cwd/x`.
   - `agentsh_guard.SYSTEM_LINK == ["/usr/bin/**", "/bin/**"]`.
6. **Order.** Keep `MAIN_FILE_RULES` as the record of the older main it names. In `test_the_file_rule_order_matches_main_around_the_new_allows`, build `wanted` as `(set(MAIN_FILE_RULES) - {"sssf-tool-write"}) | {"sssf-sessions", "sssf-agent-cache", "sssf-config-folders", "sssf-system-link"} | ...`, with the Claude-only names as now. Assert:
   - `order.index("sssf-agent-cache") == order.index("sssf-sessions") + 1`;
   - `order.index("sssf-config-folders") == order.index("sssf-config-search") + 1`;
   - `order.index("sssf-system-link") == order.index("sssf-system-read") + 1`;
   - `"sssf-tool-write" not in order`;
   - the last two rules are still `sssf-home-read` and `sssf-default-deny`.
7. **The existing link tests.** `test_relative_symlinks_elsewhere_are_denied` stays as it is and passes. Its `~/.cache/uv` case is still denied, now by the default deny. `test_a_relative_symlink_in_the_workspace_is_denied` and `test_the_sessions_folder_is_open_and_the_denies_still_hold_inside_it` stay as they are and pass. `test_absolute_links_work_as_on_main` changes in one place. Its first assertion linked to `~/.local/share/uv/python/...`, which only `TOOL_WRITE` allowed, and that allow is gone on purpose. Replace it with:
   - `r.link(f"{world.R}/.venv/bin/python", f"{world.MAIN}/adws/adw_data/agent-cache/uv-python/cpython/bin/python3.12") == "allow"`;
   - `r.decision(r.link(f"{world.R}/.venv/bin/python", f"{HOME}/.local/share/uv/python/cpython/bin/python3.12")) == "deny"`.

   Keep every other assertion in it, the `/etc/passwd` one included.

## Docs

- `.claude/skills/sssf/references/config.md`, in "Running local agents under agentsh" (line 452 on):
  - Replace the sentences from "Two gaps break other work." through "…stops `pnpm` the same way." (line 456) with what works now:
    - uv, npm, bun and Corepack read their config in the folders above the repo, for the names on the list;
    - `uv venv` works on a system Python in `/usr/bin`.
  - Say where agents' caches live: `adws/adw_data/agent-cache/`, one per repo, used only by wrapped agents. Say that a first install in a repo downloads again, and name the variables. Say that agents no longer write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm` or `~/.cargo`, and that they read only the uv-managed Pythons and tools.
  - Replace the known-limits bullet "Agents may write `~/.cache/uv`, … A guard change will give agents caches of their own." (line 481) with these limits:
    - the listed config files above the workspace are readable, so an index URL token in `uv.toml` or `pyproject.toml`, or a registry token in `~/.bunfig.toml`, reaches the agent. On a worktree run that includes the main checkout's own `pyproject.toml` and `package.json`;
    - a fresh venv needs a Python in `/usr/bin` that fits the repo's `requires-python`. Under the wrap, uv can't use your managed Pythons or download one. An existing `.venv`, or one `worktree_setup` makes, still works. Otherwise install the version into the repo's agent cache yourself, outside the wrap, from the main checkout, with the two-line command from Out of scope: `F="$(git rev-parse --show-toplevel)/adws/adw_data/agent-cache"`, then `UV_PYTHON_INSTALL_DIR="$F/uv-python" UV_PYTHON_BIN_DIR="$F/bin" uv python install --no-bin 3.13`. Say that `--no-bin` and `UV_PYTHON_BIN_DIR` keep uv from adding a `python3.x` to `~/.local/bin`. Say that agents may edit the Python this installs, so only wrapped agents' venvs and the workspace venvs quality checks run may use it. Never put `$F/bin` on your PATH, point your own uv at `$F/uv-python`, or base a venv of your own on it;
    - an absolute link may point into a system folder only in `/usr/bin` (or `/bin`), so a Python elsewhere, under `/usr/local` or `/opt` for example, can't be a venv's base;
    - one repo's wrapped runs share one agent cache, so one run's agent can plant a package that another run of the same repo installs.
  - In the existing bullet "SSSF makes the session folder's subfolders by path, …", add that SSSF makes `adws/adw_data/agent-cache` the same way, before each wrapped agent starts.
  - Keep the build pin text as it is.
- Docstrings and comments:
  - above `CONFIG_NAMES`, a comment with the source table;
  - above `FOLDER_OPS`, a comment with why these four operations and not `list`;
  - above `SYSTEM_LINK`, a comment with the reason it is safe;
  - on `cache_env`, a docstring;
  - on `folders_above` and `config_search`, one-line docstrings.
- No README or CHANGELOG mentions these rules. Check with `git grep -n "TOOL_WRITE\|CONFIG_SEARCH\|\.cache/uv"`, and fix any doc it finds outside `app_docs/`, `specs/`, `adws/adw_modules/` and `docs/designs/agentsh-local-sandbox.md`.
- `app_docs/`: don't edit it. For the documenter: any write-up there that says agents write `~/.cache/uv` or `~/.npm`, or that uv fails under `~/github`, now describes old behaviour.
- `docs/designs/agentsh-local-sandbox.md` is the hub session's record, so don't edit it. The only change under `docs/designs/` is `docs/designs/agentsh/deny-policy.py`.

## Verification

1. The repo's test check passes. It runs the skill suite, `test_agentsh_guard.py` among it: `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`. To run one file, use `tests/test_agentsh_guard.py` in place of `tests`.
2. The repo's other quality checks pass.
3. The plan's check passes. It confirms that `TOOL_WRITE` and `CONFIG_SEARCH` appear in no `.py` file under the template modules, the skill tests or `deny-policy.py`, and that the hub's `.gitignore` and `install.py` both carry `adws/adw_data/agent-cache/`.
4. `test_file_rules_equal_the_designs_deny_policy_output` passes for both harnesses. It runs `deny-policy.py /m/proj/.git <tmp>` and checks that its `file_rules` equal `policy_text`'s.
5. The builder can't install a policy, and runs under agentsh itself. So it checks everything above with the Python emulation only. If uv fails inside the wrap on the folders above the checkout, as it does on the baseline, the builder may run the suite from a copy of `.claude` under `/tmp`, as run 45742573's builder did. SSSF's own quality checks run outside the wrap, and they are the ones that count.

## For the engineer

These steps touch protected or root-owned paths, or need the service or the harvest, so they belong to the engineer and the hub session:
- Before the sudo install, the hub session checks the harvest events in `~/.local/share/agentsh-harvest/cfg1/`. Each operation uv or Corepack made on a folder above the workspace must be one of `open`, `stat`, `readlink` and `access`. If one isn't, stop and revise the spec. Don't widen `FOLDER_OPS` in the build.
- Update the hub's installed copy (`adws/adw_modules/`, protected) to the new skill commit, as in earlier runs.
- Before the sudo install, the hub session checks the rendered policy on the fork's engine (`LoadFromFile`, `NewEngineWithVariables`, `CheckFile`), for the hub and for sssf-sandbox. It checks every path in §1, §2, §3 and in the test lists. That includes each of `FOLDER_OPS` on each folder above the workspace, `list` denied on `~/github` and the worktree chain, and `symlink` with `/usr/bin/python3.12` as the target.
- Run `just agentsh-install` and the sudo lines it prints.
- After the install, check under the service:
  - `uv sync` and `uv run` in sssf-sandbox on a plain run, and in the hub on a `--worktree` run;
  - `UV_PYTHON_PREFERENCE=only-system uv venv .v`;
  - that a repo needing a Python outside `/usr/bin` fails as Out of scope says. Then check that it works after the documented `uv python install --no-bin` into its agent cache. Run `ls -la ~/.local/bin` before and after that install: `~/.local/bin` must have gained nothing, and no `python3.x` there may point into `adws/adw_data/agent-cache`;
  - `npm install` and `bun install` of a package with a `bin`;
  - Corepack `pnpm --version`, as far as this machine's broken pnpm allows;
  - `cargo --version`, through the rustup proxy with `CARGO_HOME` moved;
  - that nothing new appears in `~/.cache/uv`, `~/.npm`, `~/.cargo/registry` or `~/.local/bin`, and that `adws/adw_data/agent-cache/` fills instead;
  - that an existing `.venv` on a uv-managed Python still runs.
- Record the outcome in Next item 5 of `docs/designs/agentsh-local-sandbox.md`.
