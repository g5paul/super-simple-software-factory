# Keep a repo's secret files out of agents' reach

A repo's `.env` holds secrets SSSF agents have no use for, and today every
agent can get them two ways.

- **The environment.** `utils.py` calls `load_dotenv()` at import, so every
  key in `.env` lands in `os.environ`, and `utils.operator_env()` copies
  `os.environ` into each agent's process. Only `ANTHROPIC_API_KEY` and
  `ANTHROPIC_AUTH_TOKEN` are dropped, and only on a subscription run
  (`agent_cc.child_env`). An agent that runs `env` sees every secret.
- **The file.** An agent's Read or Grep tool reads `.env` directly, and so
  does a shell command such as `cat .env`. A repo's `forbid` rule reads shell
  commands only.

The engineer settled the design on 2026-11-06.

## The bar

Accidental exposure: an agent doing ordinary work that reads a secret file or
prints its environment, and the secret then lands in a commit, a log or the
trace. An agent set on getting a secret on the host is out of scope: agents
run as the engineer's user, so no in-process check can bound what they read.
The container and VMs are the boundary for that, since `.env` never enters
them. `references/config.md` says so plainly.

## What should change

1. **Protected files.** By default, `.env` and every `.env.*` except
   `.env.sample` and `.env.example`, anywhere in the repo or the run's
   worktree. A new `secret_files:` list in `repo.yaml` adds a repo's own
   paths or globs. SSSF finds the matching files when each agent starts.
2. **Agents' environment.** A key that `.env` set reaches an agent only when
   it is one of: `SSSF_*`; `OBS_*`; `CLAUDE_CODE_OAUTH_TOKEN`; for pi agents
   only, a provider API key pi knows, such as `FIREWORKS_API_KEY` or
   `OPENAI_API_KEY`; or a key listed under a new `agent_env:` in `repo.yaml`.
   Claude Code agents get no provider keys from `.env`. Keys that did not come
   from `.env`, such as the engineer's own shell exports, pass as today.
   Quality checks, `worktree_setup` and git keep the full environment, since
   a test suite may need the app's settings.
3. **Claude Code agents.** Pass one `--disallowedTools "Read(<path>)"` per
   protected file found at the agent's start. Checked by hand on claude
   2.1.319: with `--permission-mode bypassPermissions` and `--safe-mode`,
   such a rule makes Read and Grep refuse the file, and other files read as
   normal. Deny rules have no exceptions, so pass concrete paths, never a
   glob that would also catch `.env.sample`.
4. **pi agents.** Load `sssf_rules.ts` for every pi agent, not only when the
   repo has refuse rules. It blocks a `read`, `grep`, `find` or `ls` call
   whose path is a protected file, with SSSF's refusal text.
5. **Shell commands.** A built-in refuse rule, on for both agent kinds, that
   refuses a shell command naming a protected file by its path or basename,
   such as `cat .env`, `source .env.local` or `grep KEY sub/.env`. It goes
   through the existing refuse paths: `sssf_refuse.py` for Claude Code and
   `sssf_rules.ts` for pi. The agent reads "SSSF refused this command" and
   the run goes on. `.env.sample` and `.env.example` stay usable.
6. **Docs.** `references/config.md` documents `secret_files`, `agent_env`,
   the built-in rule, and the bar above. `docs/guide/customize.md`'s
   repo.yaml table gets both keys.

## Out of scope

- Obfuscated access, such as `cat .e''nv` or a Python one-liner that builds
  the path. The bar is accidental exposure.
- A secret file created during the run, after an agent started.
- A grep over a whole folder that holds a protected file. ripgrep skips
  hidden and gitignored files by default.
- Secrets in the engineer's own shell environment, not from `.env`.
- VM runs: `.env` never enters a VM, and `sandbox_remote` already chooses
  what crosses.

## Tests

- An agent's environment has no app key from `.env`, keeps `SSSF_*`,
  `OBS_*` and an `agent_env` key, and a pi agent keeps a provider key that a
  Claude Code agent doesn't get. A shell export with the same name as no
  `.env` key passes. A quality check still sees the app key.
- agent_cc's command has a `Read(<path>)` deny for `.env` and
  `sub/.env.local`, none for `.env.sample`, and one for a `secret_files`
  glob's match.
- The refuse rule refuses `cat .env` and `source ./sub/.env.local` through
  `sssf_refuse.py`, and lets `cat .env.sample` and `echo env` run.
- sssf_rules.ts blocks `read` of `.env` and allows `read` of `.env.sample`,
  with no repo refuse rules set.
