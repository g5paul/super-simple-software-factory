# Refuse a watched include path with a `..` the guard can't resolve

The git-directory guard watches every file an `include.path` or
`includeIf.*.path` names, resolving each to a physical path with `_REAL`
before the agent runs. When the path has a directory that doesn't exist yet
followed later by `..`, `_REAL` can't resolve it, and the guard falls back to
the lexical path. Once the agent creates the missing directory, the kernel
resolves the path through any symlink on the way, and git loads a different
file from the one the guard watches.

The reproduction, confirmed with `git config --show-origin`: the engineer's
config has `include.path = /repo/lnk/missing/../../x.inc`, and `/repo/lnk`
links to `/out/a`. The guard watches `/repo/x.inc`. The agent runs
`mkdir /out/a/missing` and writes `[core] fsmonitor = touch PWNED` to
`/out/x.inc`. Git now loads `/out/x.inc`, and `restore_git_dir` returns `[]`.
Two more cases of the same shape: `<repo>/missing/../lnk2/../x.inc`, and
`<repo>/lnk/missing/../../rl/x.inc` where `rl` links back into the repo.

What should change: before the agent runs, if any watched path has a `..`
component after a component that doesn't exist, the run stops with a breach,
as the pack cap does. The message names the include and the path, and says to
write it without `..`, or to create the missing directory first. Nothing else
changes: a path that resolves today, `..` or not, and a path with a missing
part but no `..` after it, keep exactly today's behaviour.

An earlier run tried predicting the file git would load and watching that
instead. The prediction kept missing cases, and putting back a predicted path
that turned out to be a directory or the work tree deleted the engineer's
files. That is why this design refuses instead of predicting. The case where
the agent creates the missing part as a symlink stays as documented in
`config.md`: the run stops with "NOT all put back".
