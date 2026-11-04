# Stop the run when git can't name the hooks directory

`_watched` finds the hooks directory with
`git rev-parse --path-format=absolute --git-path hooks`, through `_git`,
which falls back to an empty string when git fails. A bypass review of run
5dc86d1d found an old hole, present on `main`: when `core.hooksPath` names a
physical path longer than PATH_MAX, behind relative links, git exits 128
with "Invalid path", `hooks` comes back empty, and no hooks directory is
watched. The `core.hooksPath` value goes only into the prefix walk, never
into the watched paths. An agent then writes an executable
`hooks/pre-commit` under that path, nothing is packed or put back, and the
hook runs on the engineer's next commit.

What should change: when that `rev-parse` call fails, stop the run before
the agent with a breach, as the guard already does when git can't read the
repo's config. The breach says git couldn't name the hooks directory, gives
git's error, and says to point `core.hooksPath` at a shorter path. A repo
where the call succeeds keeps exactly today's behaviour.
