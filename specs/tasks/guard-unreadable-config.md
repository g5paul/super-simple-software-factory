# Stop the run when the git guard can't read the repo's git config

`guard_git_dir` returns None "outside a git repo", and the run then goes on
with no git-directory guard for that agent. `_watched` finds the repo's git
directories with `git rev-parse`. A bypass review in run d817e62b found that
when git can't read the config, `rev-parse` fails, `_watched` returns no
paths, and `guard_git_dir` returns None as if the repo weren't a git repo. The
agent then runs unguarded.

Two ways an engineer's setup gets there, both present on `main`:
- `include.path = <repo>/loop/x.inc` with `<repo>/loop` a symlink to itself:
  git dies with ELOOP. The agent plants `.git/hooks/pre-commit`, removes the
  loop, and the hook is kept.
- `include.path = <repo>/lnk/../x.inc` with `lnk` pointing at an unsearchable
  directory: git dies with EACCES, and an agent that makes the directory
  searchable and writes an `fsmonitor` line into `x.inc` keeps it.

What should change: tell "not a git repo" apart from "git failed". When the
run's repo root is a git repo, by the presence of a `.git` directory or file
or by `git rev-parse` exiting with git's not-a-repo error, and `rev-parse`
still fails, stop the run before the agent with a breach. The breach says git
couldn't read the repo's config, gives git's error, and says to fix the
config before running again. A repo root that really isn't a git repo keeps
today's behaviour, and so does every repo git can read.
