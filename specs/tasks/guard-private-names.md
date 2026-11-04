# Watch the private-names file

The git-directory guard doesn't watch `.git/sssf-private-names`. The skill's
pre-push hook reads that file to refuse a push whose messages or added lines
name an unlisted project. An agent that empties or deletes it turns that check
off for the engineer's next push, and the guard neither puts it back nor
reports it. A project session found this while reviewing its own setup.

Make the git-directory guard treat `sssf-private-names` like the other files it
watches in the git directory: a change during an agent call is put back, and
the run stops with a git-guard breach that names it. It lives in the common git
directory, so a worktree run must watch the same file.

The file is optional: most clones never create one. An agent that creates it
where there was none is a change too.
