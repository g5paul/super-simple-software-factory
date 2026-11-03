# Going private

Status: undecided. As of 2026-11-02 pushes are on hold while the engineer decides whether to move this repo to a private one. Commits stay local on `main` until then.

On 2026-11-03, after the leak scans below and with the pre-push scan in place, the engineer said SSSF can be public. So the project repos keep SSSF on the branch they push, and none needs fusion-harness's split for SSSF's sake. The hold itself stays until the engineer lifts it.

## Where things stand

- `origin` is g5paul/super-simple-software-factory, a public fork of disler/super-simple-software-factory. On 2026-11-02 it had no forks and no stars.
- 150 commits are public there, up to 48cc657, pushed on 2026-11-02. Everything since, starting with 880303a, is local only.
- The project repos aren't involved: they commit locally and take skill updates from this checkout, not from GitHub.

## What stops a push meanwhile

Since 2026-11-03 the engineer's user settings, `~/.claude/settings.json`, deny these in every Claude Code session on this machine, whatever the permission mode:

```json
"permissions": {
  "deny": [
    "Bash(git push *)", "Bash(git * push *)",
    "Bash(gh pr create *)", "Bash(gh pr merge *)", "Bash(gh repo create *)",
    "Bash(gh release create *)", "Bash(gh api *)"
  ]
}
```

A session that tries one is refused; `git push --help` was refused when they went in. The engineer stays logged in to `gh`, which is also git's credential helper for github.com, and pushes by running the command themselves (`! git push ...` in a session, or a terminal). The rules match commands as written, so they stop pushes nobody asked for, not a session set on getting around them, such as one that pushes from inside a script. They are not checked under SSSF agents' `--safe-mode`; each repo's `agent_rules` cover those. Remove the block when the hold lifts, or keep it so every push stays a person's.

## What a push would publish

On 2026-11-03 the hub's full history and the three project repos' (every branch) were scanned for secret-shaped strings, sensitive files and personal details.

- **No secrets anywhere.** Every key-shaped string was a variable name, like `OPENAI_API_KEY`, or a test fake, like `sk-ant-oat-test`. No `.env`, key file, `auth.json`, token file or trace database was ever committed, and all four repos ignore `.env` and `sssf.db`.
- **No work email, home path or machine name** in any tracked file.
- **The hub** commits as `g5paul@users.noreply.github.com`, on every commit of the engineer's.
- **The projects' commit email.** yt-summarizer, fusion-harness and pi-agent-observability are public forks too, each with its own `origin`. Their local commits carry the engineer's Gmail address: 183, 30 and 88 commits. None is on GitHub yet. Since 2026-11-03 each repo's own git config uses the noreply address, so new commits, SSSF agents' included, carry that. Before a first push, the old commits need their author rewritten to the noreply address, which changes every commit ID; each repo's session does that, once, just before the push.
- **The backstop.** The engineer's GitHub account has "Block command line pushes that expose my email" on, so GitHub refuses a push carrying the Gmail address.
- **The global git email** is still the Gmail address, so a new repo on this machine commits with it until it gets its own setting.
- **Not secret, but public once pushed:** the hub's docs name the project repos and describe their agent rules, run IDs and VM names.

## What stops a leak

Since 2026-11-03 the hub has a `pre-push` hook, `.githooks/pre-push`, which runs gitleaks over the commits a push would send. For a branch the remote already has, that's the commits past its copy. For a new branch, it's the commits on none of the remote's branches. A find refuses the push, and gitleaks prints it redacted. Without gitleaks the hook refuses too, so a missing scanner can't let a push through unscanned.

- **Set up once per clone:** `git config core.hooksPath .githooks`. Git doesn't run a repo's tracked hooks on its own, so a fresh clone has to opt in. This checkout has.
- **gitleaks** is 8.30.1, in `~/.local/bin`, installed from its GitHub release after checking the release's SHA-256. Its default rules found nothing in the hub's full history.
- **Tested** by running the hook as git does, since the deny rules block test pushes. A clean commit and a clean new branch passed. A planted fake GitHub token was refused on an existing branch and on a new one. With gitleaks missing from `PATH`, the hook refused.
- **To push anyway,** `git push --no-verify` skips the hook. Use it only knowing nothing was scanned.
- **The project repos** don't have it yet. Each one's session would add the same hook.

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
