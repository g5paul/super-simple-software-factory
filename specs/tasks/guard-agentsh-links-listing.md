# Judge link targets and folder listing on the new fork build

Item 12 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`. The design doc's section "The twelfth and thirteenth patches: link targets and listing, 2026-11-10" has the patches, the build and the probes.

## The problem

Run 98e91ed7 left two things open, and both need the engine to change:

1. **A venv on uv's managed Python can't be linked under the wrap.** agentsh judged a link's own path and its target by one rule list with one operation, `symlink`. Allowing the managed interpreters as targets would also have let an agent make a link inside `~/.local/share/uv/python`, the bypass round 1 of that run found. So in sssf-sandbox, whose `.venv` is on uv's managed 3.13, `uv run --with` fails.
2. **An agent can list the folders above its workspace.** In ptrace mode agentsh judged opening a folder as `open` and didn't check reading its entries. So an agent can list `~/github` and see the names of the engineer's other repos.

## Before this run: the fork build

Two fork patches in `~/github/agentsh`, made and tested outside this chain:

1. `34eff9bb` judges a symlink's target as `symlink_target` and its own path as `symlink`. Hardlinks and renames judge both ends as before.
2. `05b3777f` judges `getdents64`, and `getdents` on amd64, as `list` on the folder the descriptor names, and refuses a descriptor swap on that slot while the call is under way.

| | |
|---|---|
| Commit | `05b3777f` on `sssf/links-listing`, over `472d63cf` |
| Version | `0.20.6-dev.fork+05b3777f` |
| sha256 | `603dbf1495da87c28be301f607efd00c66fb250633334204a0b04c2f10df1747` |

The hub session ran the probes below on a private agentsh server, as the engineer's user, with copies of the installed policies. Under today's policies the new build does three things:

- It refuses every link to `/usr/bin`, `uv venv` and `uv run` among them, because `sssf-system-link` names `symlink`.
- It refuses `ls ~/github`. `/`, `/home`, the home folder, `/usr/bin`, `/proc`, `/tmp`, `~/.claude` and the workspace still list.
- **It lets an agent make a link to `~/.claude/settings.json`, `.git/config` or `.git/hooks/pre-push`.** The two deny lists, `sssf-agent-config` and `sssf-git-config`, take `WRITE_OPS`, which names `symlink` and not `symlink_target`, so a target in those files falls through to the allow rules. Build 472d63cf refuses all three. This is why the build and the policies must go in together.

## What should change

1. **`WRITE_OPS` adds `symlink_target`**, so both deny lists refuse a link that points at a file they guard. In the probes that refused all three links above, and a link to a file in the workspace still worked.
2. **`sssf-system-link` names `symlink_target`** in place of `symlink`, and `SYSTEM_LINK` adds `${HOME}/.local/share/uv/python/*/bin/python3.*`. The spec shows an agent still can't write under `~/.local/share/uv`, and that a link at a path in that tree is still refused, since its own path is judged as `symlink` and no rule allows that there. It says whether `UV_PYTHON_INSTALL_DIR`'s folder in the agent cache needs the same, and why.
3. **The home folder can't be listed.** `HOME_READ` keeps `open`, `stat`, `readlink` and `access` on `${HOME}` itself and drops `list` there; the files and folders under it that agents may read stay listable. `/` and `/home` stay listable: their entries are the system's folders and the users' names. The spec checks what in a run lists the home folder. In the probes, a Claude Code call under the wrap listed `~/.claude` and the engineer's skill folders, not `~`.
4. **The pin names the new build.** `PINNED_SHA256` becomes the sha256 above, and its comment names the commit. Nothing else in `check_machine` changes.
5. **`config.md`, in "Running local agents under agentsh"**, names this build and why a repo needs it: earlier builds let an agent list any folder it could open, and couldn't allow a venv on a uv-managed Python. It drops the two limits this closes, the managed-Python line and the listing line, and says what still holds: `/`, `/home`, the workspace and `adws/adw_data` list, `stat` still says whether a name exists, and inotify and fanotify can show names that change in a watched folder. It says the engineer installs the build and reinstalls with `just agentsh-install` together, since `check_machine` refuses while either differs.
6. **`docs/guide/install.md`**'s two pinned rows name the new build.

## The bar

The earlier bars stand. On the new build, under the rendered policies:

- `uv venv` on `/usr/bin/python3`, `uv run --with` on a venv built on a managed Python, `npm install`, `npm init -y`, `npx` and `bun add` work.
- A link to `/etc/shadow` is refused, and so is a link at `<managed install>/bin/python3.99` pointing at a file the agent wrote.
- Links to `~/.claude/settings.json`, `.git/config` and `.git/hooks/pre-push` are refused, as they are on 472d63cf.
- `ls ~/github`, `ls ~` and Python's `os.listdir` of either are refused, and a sibling repo's files stay unreadable.
- Claude Code exits 0 under the wrap.

A link target allowed beyond the three trees above is a finding. Any change to the guard beyond the six above is a finding, and so is a `config.md` line that promises more than the probes show.

## Testing

Unit tests: the rendered policy's two deny lists name `symlink_target`, `sssf-system-link` names `symlink_target` and not `symlink` with the managed-Python glob added, and `${HOME}` is opened, not listed. The tests monkeypatch `PINNED_SHA256`, so they don't see the value.

After the merge, the engineer installs the build and reinstalls both repos' policies. The hub session then checks that `check_machine` passes in both repos and reruns the probes above in each.

## This run is under agentsh

The hub stays on `local_guard: agentsh` with build 472d63cf installed. The runner checks the pin its own checkout names, so the worktree's new pin doesn't stop this run. The builder runs the suite the way run 98e91ed7's did, from a copy outside the repo, since `adws/tests` and the skill suite read the hub's `.env` at import.

## Out of scope

The fork's code, reviewed and tested outside this chain. The seccomp-notify file handler, which SSSF doesn't use and still judges a link's target as `symlink`. pnpm's two `npm_config_*` variables. The pin check's two old holes (Next item 1). pi's policy.
