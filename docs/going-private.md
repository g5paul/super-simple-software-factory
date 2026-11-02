# Going private

Status: undecided. As of 2026-11-02 pushes are on hold while the engineer decides whether to move this repo to a private one. Commits stay local on `main` until then.

## Where things stand

- `origin` is g5paul/super-simple-software-factory, a public fork of disler/super-simple-software-factory. On 2026-11-02 it had no forks and no stars.
- 150 commits are public there, up to 48cc657, pushed on 2026-11-02. Everything since, starting with 880303a, is local only.
- The project repos aren't involved: they commit locally and take skill updates from this checkout, not from GitHub.

## What can't be undone

- **A fork of a public repo can't be made private.** GitHub locks its visibility while it's in the parent's fork network.
- **Pushed commits stay in the fork network.** GitHub documents that a fork's commits can stay reachable by hash through the parent repo, even after the fork is deleted or force-pushed. Only GitHub Support can purge them.
- **Copies may exist.** Anyone who cloned the fork, or a cache that read it, keeps what they have. With no forks or stars that's probably no one, but nothing confirms it.

## Steps, if going private

1. **Create a private repo** under the engineer's account, for example `g5paul/sssf`. Make it a new repo, not a fork, so it sits outside disler's network.
2. **Push the full history to it:** `git remote add private <url>` then `git push private main`. Nothing is lost.
3. **Make it `origin`.** Rename the old `origin` (for example to `public`), rename `private` to `origin`, and keep `upstream` pointing at disler for fetches.
4. **Retire the public fork.** Archive it to freeze it as read-only, or delete it to remove its page and branches. Deleting doesn't remove the network copies above.
5. **Only if something sensitive was pushed:** scan the public history first (`.env` values, tokens, internal names or paths), then ask GitHub Support to purge it from the fork network.
6. **Update the local rules.** `CLAUDE.local.md`'s pushing section names the fork as `origin`; point it at the new repo and lift the hold.

Steps 1, 4 and 5 act on the engineer's GitHub account, so each is confirmed with the engineer before it runs, or done by the engineer in GitHub's settings.

## If staying public

Lift the hold in `CLAUDE.local.md`, then push `main` as before. The local commits since 48cc657 go up together.
