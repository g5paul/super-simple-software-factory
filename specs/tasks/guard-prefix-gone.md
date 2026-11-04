# Stop the run when a prefix of a watched path that resolved no longer does

Run 6bbc5ca1 made the guard record where every prefix of each watched path
resolves before the agent, and stop the run with "NOT all put back" when a
prefix resolves somewhere else after it. A review of that change found an old
hole, present on `main` and on the change: a prefix that resolved before and
no longer resolves at all is not counted as moved.

The reproduction: `~/.gitconfig` has `include.path = $HOME/d/../x.inc`, `~/d`
is a real directory, and `~/x.inc` holds `core.hooksPath = /dev/null`. The
agent runs `rmdir ~/d`, or replaces `~/d` with a regular file. Git then
silently stops loading `~/x.inc`, and the protective line is gone, but
`restore_git_dir` returns `[]` and the run goes on.

What should change: a prefix that resolved before the agent and doesn't
resolve after it, because it is now missing, a regular file, or anything
else the walk can't pass through, counts as moved. The run stops with "NOT
all put back" and names it, the same as a prefix that resolves somewhere
else. Nothing is written through it, as now. A prefix that didn't resolve
before keeps today's behaviour, and so does every prefix that resolves the
same.
