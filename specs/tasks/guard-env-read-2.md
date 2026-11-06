# Close the two broken promises in the secret-file guard

Run 13b4d30c built `specs/tasks/guard-env-read.md` on this branch (bb1b840).
Its bypass review found nothing blocking, but two of the spec's promises
break in normal use. Fix both. Everything else that run built stays as it is,
and so do its bar and out-of-scope list.

## 1. Keys exported by `just` still reach agents

The stamped justfile has `set dotenv-load` (`templates/justfile:7`), so a
chain started through any `just` recipe has every `.env` key exported before
Python starts. `utils` then sees them as the engineer's own shell exports,
`DOTENV_KEYS` is empty, and `agent_env()` strips nothing. The reviewer proved
it with real just: `APP_SECRET` from `.env` reached the agent.

What should change: a key counts as from `.env` when `.env` names it, whether
or not it was already in the environment when the ADW started. Read the
names from the file itself, as `dotenv_values` does, not from what
`load_dotenv` changed. A shell export whose name `.env` also sets is
stripped too; the engineer accepted that on 2026-11-06, since that name is a
secret the repo keeps in `.env`. A shell export whose name `.env` doesn't
set passes as today. `references/config.md` says this.

Tests: with `.env` setting `APP_SECRET` and the same key already in
`os.environ` before `utils` loads, as `just` leaves it, an agent's
environment has no `APP_SECRET` and a quality check still has it. A launch
through the stamped justfile's own recipe, with real just when it is on
PATH, shows the same.

## 2. A deny path with pattern characters denies the wrong file

`agent_cc._command` passes `Read(/<abs path>)` with the raw path. Claude Code
reads the rule as a gitignore-style pattern, so a path with `[`, `]`, `*`,
`?`, `!`, a backslash or a space can deny a different file and leave the
real one readable.

Checked by hand on claude 2.1.319, `--permission-mode bypassPermissions`,
`--safe-mode`, in a folder named `weird [x] dir`:

- `Read(//<abs>/weird [x] dir/.env)`, raw: the agent read the file.
- `Read(//<abs>/weird\ \[x\]\ dir/.env)`, each of `[ ] * ? ! \` and space
  escaped with a backslash: the read was denied.

What should change: escape each of those characters with a backslash before
building the rule. Keep one rule per file.

Tests: the rule for a file under `weird [x] dir` is the escaped spelling
above; a path with `*`, `?`, `!` and a backslash escapes each; an ordinary
path is unchanged.

## Out of scope

The old holes run 13b4d30c listed stay out of scope: case variants on a
case-insensitive filesystem, `.env` behind a symlinked folder, a grep over a
whole folder, obfuscated names and hard links.
