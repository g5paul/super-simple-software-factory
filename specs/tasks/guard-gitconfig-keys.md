# Name the keys of a user-level git config change the guard puts back

The git-directory guard saves `~/.gitconfig`, `~/.config/git/config`, the
global ignore files and any included config file before each agent, and puts
back any change after it. These files are shared with everything else the
engineer runs. A project session reported that a change made during a run by
something other than the agent, such as `gh auth setup-git` in another
terminal or another Claude session, is put back too, and the breach blames
the agent. The engineer's change is lost, and nothing says it may not have
been the agent's.

The guard runs as the engineer's user, so it can't tell who wrote the file.
The change must still be put back and still stop the run: an agent's
`core.fsmonitor` or `core.hooksPath` line left in `~/.gitconfig` would run on
the engineer's next git command.

What should change: for each user-level git config file the guard puts back
(the watched config files outside the repo's git directory), the breach names
the file, says the change may have come from outside the agent, and lists the
config keys that were added or removed, such as
`+ credential.https://github.com.helper` or `- core.fsmonitor`. It never shows
a value. The engineer reads the keys, recognises their own change, and makes
it again with the command they used. A global ignore file that changed is
named with how many lines were added and removed, and no line is shown. Cap
the keys listed per file and in all, and say when some were left out. The
repo's own `.git` paths keep today's message. The guard writes no new file.

Two earlier runs tried a copy of the file on disk, then the changed lines
with secret values masked. Every place to write a copy could be pointed into
a watched tree, and the masking needed a parser and regexes that an agent's
long line could stall. Get the key names from git itself, such as
`git config --file <saved copy> --name-only --list` on each version, with a
time limit, rather than parsing the file in Python. The saved before-version
is already held in memory.

The reproduction: while an agent runs, a process that isn't the agent runs
`git config --global credential.https://github.com.helper '!gh auth git-credential'`.
Today the line is gone after the run, and the breach doesn't name it.
