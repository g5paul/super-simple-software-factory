# Record a symlink that a later `..` cancels on a watched path

The git-directory guard records every symlink on the way to a watched path in
`guard["links"]`, saves where each points, and stops the run with "NOT all put
back" if one points somewhere else after the agent. A bypass review in run
2d444669 found an old hole, present on `main`: the walk that collects the
links runs on `posixpath.normpath(p)`, which removes `lnk/..` before the walk
sees `lnk`. The kernel doesn't: it follows `lnk` first, then takes its parent.

The reproduction: `~/.gitconfig` has `include.path = $HOME/lnk/../x.inc`,
`$HOME/lnk` links to `out/a`, and `out/x.inc` exists, so git loads
`out/x.inc`. The guard watches `out/x.inc`, and `guard["links"]` is empty. The
agent runs `rm ~/lnk; ln -s evil/a ~/lnk` and writes
`[core] fsmonitor = touch /nonexistent/pwned` to `evil/x.inc`. Git now loads
`evil/x.inc`, and `restore_git_dir` returns `[]`.

What should change: collect the links on the way to each watched path by
walking it the way the kernel does, component by component as written,
following each symlink before applying a later `..`. Every symlink the kernel
passes through is then recorded, and a retargeted one stops the run as any
other link on the way does today. A path with no `..`, and the paths the
guard watches, keep exactly today's behaviour.
