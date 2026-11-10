# Pin the agentsh build that judges renameat and new links

Item 5 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`.

## The problem

`PINNED_SHA256` in `agentsh_guard.py` is build `241cd393`'s sha256. An independent review of the ninth fork patch found three holes in that build, and the design doc's section "The review and three fixes, 2026-11-10" has the probes. The worst: the tracer never judged `renameat`, which `mv` makes through glibc 2.39, so an agent could move a file outside every allow into its workspace and read it there. `mknod` and `lchown` weren't judged either. A link path whose folder was missing was judged as text, and a link target could be rewritten after the check.

The engineer installed the fixed build on 2026-11-10. Since then `check_machine` refuses every run under `local_guard: agentsh` with `/usr/bin/agentsh is not the pinned build`.

| | |
|---|---|
| Commit | `3a36c68f`, `fix(ptrace): judge a new symlink at syscall exit`, on `sssf/races`, with `dea39254` (renameat, mknod, lchown) and `8ac79c33` (a missing link folder) under it, on `sssf/patched` |
| Version | `0.20.6-dev.fork+3a36c68f` |
| sha256 | `0e4430b5eb9f77ddb8014bcc6cd1fad3c8eda6a4deeb7b041777f74c46440886` |

Two builds from `3a36c68f` with the recipe in the design doc gave the same bytes. The build also carries the ninth patch, so a link's target now reaches the rules as the file it leads to, resolved from the link's folder, and a relative link in an allowed place works. On the installed build, under sssf-sandbox's policy: `mv`, `rename`, a direct `renameat` and `mknod` of a canary are refused; `ln -s` to the canary, absolute or relative, is refused; a relative `ln -s` in the workspace, `uv venv` and npm's `.bin` link work. The check set held as before: 98 probes, 32 must-work lines, the 24-case corpus and 28 network verdicts.

## What should change

1. **The pin names the installed build.** `PINNED_SHA256` becomes the sha256 above, and its comment names `3a36c68f` on `sssf/races`. Nothing else in `check_machine` changes.
2. **`config.md`, in "Running local agents under agentsh"**, says which build a repo needs and why: this one, since earlier builds let an agent move a file it may not read into its workspace with `mv`, besides the `tar -x` and namespace reasons already there. With the ninth patch in:
   - The opening paragraph no longer says a venv's relative link is denied, or that `uv venv`, `uv run --with`, `uvx`, npm and bun fail on it. The uv config lookup above the repo and Corepack's lookup stay, since they still break.
   - The limit "A symlink with a relative target is denied, wherever it's made" goes. In its place: a link's target is judged as the file it leads to, resolved from the link's folder through any links; a target through `/proc` or `/dev/fd`, or with `..` after a folder that doesn't exist, fails closed.
   - The limit "An absolute link target is judged by its text" goes, for the same reason.
   - A new limit: the check happens when the link is made. A link moved later, or a folder on its path swapped for a link, can lead somewhere else, which matters only to a tool that follows it outside the wrap, since every read inside is judged at the file it reaches.
   - A new limit: on folders that ignore case, such as `/mnt/c`, `.SSH` reaches `.ssh`, and a rule naming `.ssh` misses it.
3. **`docs/guide/install.md`**'s pinned-versions row for the agentsh build names `0e4430b5…40886` and `3a36c68f`.

## The bar

The earlier bars stand. A pin that doesn't match the sha256 above is a broken promise. Any change to `check_machine` or the rest of the guard beyond the constant and its comment is a finding. A `config.md` line that promises more than the probes above showed is a finding.

## Testing

The tests monkeypatch `PINNED_SHA256`, so they don't see the value. The hub session checks the real `check_machine` against `/usr/bin/agentsh` on this machine after the run.

## This run is under the text guard

`check_machine` refuses while the pin is wrong, so this run can't start under agentsh. The engineer set the hub's `local_guard: text` for this run only, and sets it back after the merge. The text checks are on, as they were for every guard run before the hub moved to agentsh.

## Out of scope

The pin check's two old holes (an edit to the project's `agentsh_guard.py`, a service on an older binary), which are Next item 1. The tool-config task. The policy rules, pi under the wrap, sudo. The fork's code, which was reviewed and tested outside this chain.
