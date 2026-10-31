# Install

`/sssf install` — stamp the entire factory out of the skill and into the current working directory.

## Run it

```bash
uv run .claude/skills/sssf/scripts/install.py
```

Run from the **target repo root** — the cwd is where everything lands. If the skill lives in your user scope, the path is `~/.claude/skills/sssf/scripts/install.py`.

To run every agent on Claude Code instead of Pi, add `--coding-agent claude_code`. The installer then stamps `templates/sssf.config.claude_code.yaml` as the roster. It has the same prompts, purposes, and `writes` boundaries as the Pi roster, with Anthropic models and no pi extensions. It uses your `claude` subscription login, so `.env` needs no API key. Check the login with `claude auth status`.

To run every agent on Pi signed into a ChatGPT subscription, add `--chatgpt`. The installer stamps `templates/sssf.config.chatgpt.yaml`: the same prompts, purposes, and `writes` as the Pi roster, with gpt-6.1-sol for the planner, builders, and reviewers, and gpt-6.1-terra for the scout and documenter. Sign Pi in first with `pi`, then `/login`, then `openai`, and check it with `pi auth check --provider openai --json`, which must report `"authType": "oauth"`. Set `SSSF_PI_AUTH=subscription` in `.env` so a lapsed sign-in fails the run instead of billing an API key.

## What gets stamped

`install.py` copies `templates/` into the cwd:

| Stamped | From | Tracked? |
|---|---|---|
| `adws/adw_sssf_config/sssf.config.yaml` | `templates/sssf.config.yaml` | yes — the agent roster |
| `adws/adw_sssf_config/repo.yaml` | `templates/repo.yaml` | yes — the repo's own rules, such as fast-forward-only merges. Yours to edit |
| `.env.sample` | `templates/env.sample` | yes |
| `adws/adw_*.py` | `templates/adws/` | yes — the twelve starter ADWs |
| `adws/adw_modules/` | `templates/adws/adw_modules/` | yes — all low-level logic |
| `adws/adw_data/prompt_engineering/{planner,builder,scout,reviewer,documenter}/` | `templates/prompt_engineering/` | yes — **the user-owned home for prompts** |
| `adws/adw_data/harness_engineering/` | `templates/harness_engineering/` | yes — **the user-owned home for pi extensions** |
| `justfile` | `templates/justfile` | yes — starter recipes: `just demo`, the workflows, the trace reads, `just obs` |
| `adws/adw_data/sessions/`, `adws/adw_data/sssf.db` | created at runtime | no — gitignored |

The two `*_engineering` dirs mirror the two config keys of the same name: `prompt_engineering` is what an agent is told, `harness_engineering` is what its harness can do. Both are yours the moment they are stamped. Edit them in `adws/adw_data/`, never back inside the skill.

`harness_engineering/` ships with `subagents.ts` — the pi extension backing `subagent_create` / `_continue` / `_list` / `_remove`, wired to the planner and scout in the starter roster.

## Idempotency

Re-running is safe. `install.py` skips **every** file that already exists — your config, your prompts, and previously stamped code alike — and reports what it skipped, so a second run doubles as a drift check. `--force` overwrites ALL existing stamped files, including `sssf.config.yaml`, `quality.py`, and `prompt_engineering/`. To bring a repo up to a newer skill, use `--update` instead (next section).

A repo's own `justfile` or `.env.sample`, there before SSSF, is skipped and recorded as kept, the same as `--update --keep` would record it. The first update leaves it alone, and it is only flagged again when the skill's template changes. A repo with its own justfile can load SSSF's recipes as a module instead: add `mod sssf 'sssf.just'` to it, then install or update. SSSF stamps its recipes into `sssf.just`, records it, and keeps it current on each update, like the rest of its code. Run them as `just sssf demo`, `just sssf sessions`, and so on. The repo's own recipes stay in its justfile, which SSSF never records or changes.

## Update an installed repo

When the skill changes, set `SSSF_SOURCE` in the repo's `.env` to the skill's source directory, and run:

```bash
just sssf-update
```

If the repo's own justfile has `mod sssf 'sssf.just'`, SSSF's recipes run as a module, so every one takes the `sssf` prefix: `just sssf sssf-update`. The same goes for `just demo`, `just sessions`, and the rest. Check the justfile for the `mod sssf` line before running any of them.

It refuses to start while the tree has uncommitted changes or a chain process is alive, so the update gets its own commit. The chain check reads this repo's own `processes` table in `adws/adw_data/sssf.db`, so a run in another repo does not block it, even though `pgrep` shows it. It also refuses when the source is on a branch with commits that are not on the source's `main`, because it copies whatever the source has checked out. Ask the hub to switch the source back to `main`. `SSSF_ALLOW_UNMERGED=1` takes the branch anyway, for testing unmerged work in a test repo. Then it does the two steps below, runs `--update` a second time to confirm nothing is left, and prints the skill commit it copied from. The steps by hand:

```bash
# 1. refresh the repo's copy of the skill from its source. The excludes keep
#    each repo's own visualizer packages and built page, so a running
#    `just obs` keeps working.
rsync -a --delete --exclude apps/visualizer/node_modules --exclude apps/visualizer/dist \
  /path/to/super-simple-software-factory/.claude/skills/sssf/ .claude/skills/sssf/

# 2. update the stamped files
uv run .claude/skills/sssf/scripts/install.py --update
```

`install.py` records a hash of every file it stamps in `adws/adw_sssf_config/stamp.json`. Commit that file with the install. `--update` uses it to decide:

| The file in the repo | `--update` |
|---|---|
| is missing | adds it (`+`) |
| already matches the skill | leaves it (`up to date`) |
| still matches its recorded hash, so the repo never changed it | replaces it (`~`) |
| differs from its recorded hash, but the skill's version has not changed since | keeps it and lists it under `customized` (`=`). `quality.py` is the usual case: the repo is meant to edit it |
| differs from its recorded hash, and the skill's version has changed since | keeps it and flags it for review (`!`), with a `diff` command to compare |

So a tuned roster, the real commands in `quality.py`, and edited prompts survive an update, and `!` appears only when the skill has something new for a file the repo changed. For each `!` file, choose one. Both are flags of `--update`, and both repeat:

```bash
uv run .claude/skills/sssf/scripts/install.py --update --take <path>
uv run .claude/skills/sssf/scripts/install.py --update --keep <path>
```


- `--take <path>` replaces the repo's version with the skill's.
- `--keep <path>` keeps the repo's version and records it as reviewed. It stays quiet until the skill's version changes again.
- To have both, which is the usual case for `quality.py`: run the printed "the skill's change" command, which shows only what the skill changed in that file, copy that into the repo's file by hand, keeping the repo's own commands, then `--keep <path>`. The other printed `diff` is the repo's whole file against the skill's, so it mixes that change with every edit the repo made. Never `--take` a file you customized: it replaces your commands with the skill's placeholders.
- A repo's own file that SSSF never merged its template into, such as an app's `.env.sample` with no SSSF lines: the skill's change may extend lines the file doesn't have. Add what the change needs, under a short SSSF heading if the file has no SSSF section, then `--keep <path>`.

A repo installed before `stamp.json` existed has no record. Its first update replaces SSSF's own code (`adws/adw_modules/` except `quality.py`, and the starter `adw_*.py`), because a repo is not meant to edit it. It keeps every other file that differs, prompts included, because it cannot tell an edit from an older template. The output marks those replacements `(SSSF code, no stamp record yet)`. Check each `!` file with the printed `diff` command: `--take` the ones you never changed, and `--keep` the ones you did, such as `quality.py`. After that, each file has a record, and `!` means the skill changed something you also changed.

`stamp.json` also records the skill commit the update came from, under `skill`, with `+dirty` when the source had uncommitted changes. It is the repo's record of which skill it is on, so it changes even when an update only touches docs.

### Finish the update

Run the update the way the repo takes any change: on a short branch cut from `main`, then fast-forward `main` to it and delete the branch. A repo whose own rules say otherwise follows its rules. Never run it while a chain is going: it refuses anyway.

1. Resolve every `!` file with `--take` or `--keep`, and run the update again until `to review` is 0.
2. Read `git status` and `git diff`. Expect the files the update listed, `stamp.json`, and the skill copy under `.claude/skills/sssf/`. A file you did not expect is a question for the hub, not something to commit.
3. If the update listed `repo.yaml` and the repo's copy had no `max_worktree_runs` before it, decide that value now; the skill's default lets 2 worktree runs go at once. For a `!` `repo.yaml`, copy in the skill's change first, then `--keep` it. Is `quality.py` safe to run twice at the same moment? Two worktree runs check in two folders at once, so a check that binds a fixed port, writes a fixed temp path, or uses one shared test database breaks the other run's checks. Read the repo's `quality.py` and write `max_worktree_runs: 1` or `2` into `repo.yaml`, with a one-line comment saying why. That edit goes into the update commit. The same goes for `worktree_setup`, if the repo's copy had none: What does a fresh checkout need before the checks can run? A worktree run's folder is a new checkout, so it has none of the untracked dependencies yours has, such as `node_modules`. List the commands that install them under `worktree_setup` in `adws/adw_sssf_config/repo.yaml`, the way the README installs them, for example `cd web && bun install --frozen-lockfile`. They must only create gitignored files. A Python repo on uv needs none.
4. Commit the update on its own: `git add -A && git commit -m "Update SSSF to skill <sha>"`, with the sha the update printed. A file the update just added (`+`) that you edit straight away, such as `repo.yaml`, can go into this commit with your edit, since it never existed without it.
5. Run `uv run adws/adw_quality.py "check after the SSSF update"`. A block reported as `not configured` is still on its placeholder, and checks nothing until you wire it in `quality.py`.
   Quality never starts an agent, so it cannot tell you the agents can still sign in. On a Claude Code roster, check that too, with the agents' own environment: `just login-check` (`just sssf login-check` with `mod sssf`). It runs the same check a chain runs before its first agent.
6. Report the commit, the update's counts, the `max_worktree_runs` and `worktree_setup` you chose, anything flagged, and the quality result to the hub: the Claude session that works in the SSSF skill repo and sent the update ([onboard.md](onboard.md), "Two sessions"). With no hub session, report to the engineer.

## Run the operator session in a container

On the host, Claude Code's auto mode blocks the command that launches a chain, so the engineer approves each launch. `--container` stamps a Podman setup that runs the operator session with `--dangerously-skip-permissions` instead. Inside it, the session can reach only the repo and `~/.config/sssf`, both mounted at their host paths, so the trace and `just obs-all` on the host see every run. It covers the operator session and every agent it starts. It does not give each run its own branch: a run still commits to the branch that is checked out.

It needs Podman and a Claude Code roster. The image has no Pi.

```bash
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code --container   # new install
uv run .claude/skills/sssf/scripts/install.py --container                              # add it to an installed repo
```

| File | What it does |
|---|---|
| `container/Containerfile` | The image: Ubuntu, git, sqlite3, uv, just, Claude Code, a C compiler with Python headers, and tzdata. **The repo's to edit**: add the tools `quality.py` runs, such as bun or node. An update keeps your edits, like `quality.py` |
| `container/build.sh` | Builds the image as `localhost/sssf-operator-<repo>`. Run it again after editing the Containerfile |
| `container/run.sh` | Starts the session. Builds the image on first use |

Keep `container/` out of the repo's package builds, as with `adws/` and `.claude/`. A Python sdist includes it otherwise.

The engineer sets it up once, in their own terminal:

1. Make a token, unless `~/.config/sssf-container/token.env` already exists. Every repo shares that file, and a token lasts about a year. To make one: `claude setup-token`. It opens a browser sign-in and prints a token that starts with `sk-ant-oat`. Save it without echoing it:

   ```bash
   mkdir -p ~/.config/sssf-container && chmod 700 ~/.config/sssf-container
   read -rs -p "Paste token, then Enter: " T && printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$T" > ~/.config/sssf-container/token.env && unset T && chmod 600 ~/.config/sssf-container/token.env
   ```

   `SSSF_CONTAINER_TOKEN` points `run.sh` at another file. The token signs in to the subscription: keep it out of the repo and out of chat.
2. Start the session: `container/run.sh`. It skips Claude's first-run login menu, since the token already signs it in. Answer yes to trusting the folder, and accept the bypass-permissions warning. A home volume, `sssf-operator-home`, remembers both.
3. Check that the session's shells get the token: `! printenv | grep -c '^CLAUDE_CODE_OAUTH_TOKEN='` must print `1`. A Claude Code session does not pass its own login to the commands it runs, so `run.sh` exports the token through `CLAUDE_ENV_FILE`. Without it, every agent would end "Not logged in", and the run stops before the first agent with that advice.

Then operate as usual. Each launch runs with no prompt, and the agents bill to the subscription: every agent's `apiKeySource` is `none`.

A container session cannot push. `run.sh` mounts neither `~/.ssh` nor the host's git config, so the host's credential helper isn't there. The exception is a remote URL with a token in it, which works anywhere: check `git remote -v` before relying on this for a repo with a no-push rule.

Only one session works in a repo at a time. While the container session has a task, give the repo's host session nothing, since both write to the same checkout.

## Move a branch install to main

SSSF belongs on `main` ([onboard.md](onboard.md), step 1). A repo that installed it on a branch moves it like this:

1. List what the branch has that `main` lacks: `git log --oneline main..<branch>` and `git diff --stat main <branch>`.
2. Pick the SSSF commits: the install, every "Update SSSF" commit, and every commit the install needs to pass on its own. That last group is easy to miss. `quality.py` runs the repo's own commands, and they can depend on files SSSF did not stamp: in pi-agent-observability, the typecheck block needed the repo's `tsconfig.json`, `package.json`, `bun.lock`, and the type fixes that made it pass.
3. Ask the engineer about every other commit on the branch, such as features built there. They decide whether those go to `main` too.
4. If `main` is an ancestor of the branch and every commit goes, fast-forward `main` to it. Otherwise cherry-pick the chosen commits onto `main`, oldest first.
5. On `main`, check that `.claude/skills/sssf/SKILL.md` and `adws/adw_sssf_config/stamp.json` exist, that a `mod sssf` justfile still has its `mod` line, and run `uv run adws/adw_quality.py "check SSSF on main"`. Every block that passed on the branch must pass on `main`.
6. Fix the repo's own docs that say SSSF lives on the branch. Whether to delete the branch is the engineer's call.

A session that already has `/sssf` keeps it through the switch. New sessions get it on `main` and on every branch cut from it.

## Post-install checklist

0. **Restart the session.** Claude Code registers `/sssf` when a session starts, so a session that was running before the install cannot load it. Restart it in the repo (`claude --continue` keeps the conversation). If SSSF lives on a branch instead of `main`, start every operator session with that branch checked out: switching branches mid-session does not register the skill.
1. **Env** — `cp .env.sample .env`, then set `OPENROUTER_API_KEY` in `.env`. (Claude Code agents need no key: they run on the operator's `claude` login, and `agent_cc` strips `ANTHROPIC_API_KEY` from them unless `SSSF_CLAUDE_AUTH=api_key`.)
2. **Pi is installed and on PATH** — `pi --version`. Set `PI_PATH` in `.env` if it is not.
3. **The model resolves** — the config's default `gemini-3.6-flash` must be a registered id in `~/.pi/agent/models.json`. Check with `pi --list-models` or read the file directly; see `references/config.md` for model resolution.
4. **Gitignore** — `install.py` appends `adws/adw_data/sessions/`, `adws/adw_data/sssf.db*`, and `.env` for you; confirm they landed. All three are runtime or secrets and must never be committed.
5. **Git repo** — ADWs that end in a commit phase call `git_helper.commit_all`, which raises if the cwd is not a git repository. Run `git init` and make a first commit before using `adw_plan_build.py`, `adw_plan_build_test.py`, or `adw_simple_sdlc.py`. `adw_document.py` needs one too: it measures the change with `git diff` against a base ref (`main` by default, `--base` to override).
6. **Keep SSSF out of the project's own tools.** The install adds about 150 files of Python, TypeScript, and Vue under `adws/` and `.claude/`, and they don't follow your project's rules. A linter, formatter, or type checker that runs on the whole repo then fails on SSSF's code, not yours. In yt-summarizer, CI's `ruff check .` and `ruff format --check .` failed with 434 errors and 64 files, all in those two folders. A package build can also take them in: its first `uv build` put 148 SSSF files into the sdist. `quality.py` names your paths, so the chain passes, and CI fails later. Exclude both folders where your tools read the whole repo.

   Exclude `specs/` and `app_docs/` from formatters and linters too. They are SSSF's records of each run, not code, and ruff 0.16 also formats Python blocks inside Markdown. In one yt-summarizer run, the committed spec failed `ruff format --check .`, and the builder's `ruff format .` rewrote it. Package builds can leave these two folders alone; they are small and harmless.

   The settings:

   | Tool | Where |
   |---|---|
   | ruff | `pyproject.toml`: `[tool.ruff]` `extend-exclude = ["adws", ".claude", "specs", "app_docs"]` |
   | eslint (flat config) | `eslint.config.js`: `{ ignores: ["adws/**", ".claude/**", "specs/**", "app_docs/**"] }` |
   | prettier, biome | `.prettierignore`, or `files.ignore` in `biome.json` |
   | tsc | `tsconfig.json`: `"exclude": ["adws", ".claude"]` (tsc reads no Markdown) |
   | hatchling sdist | `pyproject.toml`: `[tool.hatch.build.targets.sdist]` `exclude = ["/adws", "/.claude"]` |
   | setuptools sdist | `MANIFEST.in`: `prune adws` and `prune .claude` |
   | npm package | the `files` field in `package.json`, or `.npmignore` |

   `install.py` does not edit these files: they belong to the project. A wheel limited to your package is already clean. The sdist still carries a few small SSSF files at the top level (`justfile`, `.env.sample`, `specs/`, `app_docs/`). They are harmless, and you can exclude them the same way.

7. **Parallel runs** — Is `quality.py` safe to run twice at the same moment? Two worktree runs check in two folders at once, so a check that binds a fixed port, writes a fixed temp path, or uses one shared test database breaks the other run's checks. If it isn't, set `max_worktree_runs: 1` in `adws/adw_sssf_config/repo.yaml`.
8. **Worktree setup** — What does a fresh checkout need before the checks can run? A worktree run's folder is a new checkout, so it has none of the untracked dependencies yours has, such as `node_modules`. List the commands that install them under `worktree_setup` in `adws/adw_sssf_config/repo.yaml`, the way the README installs them, for example `cd web && bun install --frozen-lockfile`. They must only create gitignored files. A Python repo on uv needs none.
9. **Smoke test** — `just demo` runs two cheap read-only workflows back to back, or run the smallest ADW directly:

```bash
just demo                                                    # both, end to end
uv run adws/adw_prompt.py "reply with a one-line summary of this repo"   # the raw form
```

Green means the whole path works: config validated, session minted, Pi ran, envelope parsed, events landed in `adws/adw_data/sssf.db`. Verify the trace exists before trusting anything larger:

```bash
sqlite3 adws/adw_data/sssf.db "select adw_id, status from sessions order by started_at desc limit 1;"
```

If the smoke test fails, fix it before composing chains — every multi-agent ADW rides on this exact path.
