# Read the agent dir pi reads, however PI_CODING_AGENT_DIR is written

In the operator container, `agent_pi._pi_auth_json` turns
`PI_CODING_AGENT_DIR` into the dir whose `auth.json` and `models.json` the
stored-login check reads. Its docstring says the check must read what pi
reads, and it doesn't always. Runs 3ca0a481 and 5e778abf's bypass reviews
found the mismatch, and each listed it as an old hole.

pi 1.4.0 resolves the variable in `getAgentDir` (`dist/config.js`) through
`normalizePath` (`dist/utils/paths.js`):
- `~` alone is the home dir, and `~/rest` is `join(home, rest)`.
- A `file://` URL becomes a path through `fileURLToPath`.
- Anything else is used as written. `~user/x` is not expanded, so pi reads
  `<cwd>/~user/x`, a relative path.
- pi then `join`s `auth.json` or `models.json` onto it. Node's `join` folds
  `.` and `..` as text, so `/a/link/../b` is `/a/b` whatever `link` is.

SSSF uses `os.path.expanduser`, which also expands `~user`, and builds a
`Path` that leaves `..` to the kernel. With `~user/x`, or with a symlink
before a `..`, SSSF checks one file while pi reads another. The `file://`
case gives a message that names the wrong path.

What should change: the check works out the agent dir the way pi 1.4.0
does, case by case, including how `join` folds the path, and reads both
files from that dir. A value that resolves to a relative path still stops
the run with today's message, since the worktree's cwd isn't known when the
check runs. That now includes `~user/x`. Tests cover each case against what
pi's code gives, including a symlink before `..`. An unset variable keeps
reading `~/.pi/agent`, as today.

If `normalizePath` does more than this in pi 1.4.0, the check follows that
too. Any case it can't match exactly stops the run rather than guessing.
`references/config.md` keeps its limits accurate, with no new ones unless
the review finds a case the check can't match. A host run, a VM run, and a
Claude Code roster in the container keep exactly today's behaviour.
