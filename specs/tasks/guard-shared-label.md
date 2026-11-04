# Label only files outside the repo as shared in the git-guard breach

When the git-directory guard puts back a git config or ignore file outside
the repo's git directory, the breach labels it as outside the repo and shared
with everything the engineer runs, says the change may have come from outside
the agent, and lists the keys or line counts that changed. If every changed
path is such a file, the breach opens with "A git config or ignore file
outside the repo changed while the agent ran".

A bypass review found that the label also lands on files inside the repo's
working tree. `_watched` marks a watched path as shared when it is outside
the git directories, but it doesn't leave out `run.repo_root`. So a file the
agent itself can edit, such as `team.gitconfig` in the work tree named by
`include.path`, or `.myignore` named by `core.excludesFile`, gets the label,
and the message points blame away from the agent. A relative include, such
as `../team.gitconfig`, is left unmarked only because its path is built
through `.git/..`.

Mark a path as shared only when its physical path is outside both the git
directories and the repo's working tree, however the path was written:
absolute, relative through `.git/..`, or through a symlink. Every file the
guard watches is still watched, put back and reported exactly as today; only
the label and the lead sentence change. In a worktree run, the work tree to
leave out is the run's own.

The reproduction: `git config include.path "$PWD/team.gitconfig"` in the
repo, and the agent appends `[alias] z = y` to `team.gitconfig`. Today the
breach says the file is outside the repo and may not be the agent's.
