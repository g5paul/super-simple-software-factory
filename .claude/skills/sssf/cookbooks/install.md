# Install

`/sssf install` — stamp the entire factory out of the skill and into the current working directory.

## Run it

```bash
uv run .claude/skills/sssf/scripts/install.py
```

Run from the **target repo root** — the cwd is where everything lands. If the skill lives in your user scope, the path is `~/.claude/skills/sssf/scripts/install.py`.

If the repo's skill copy came from a git checkout, run the install with `SSSF_SOURCE=<that checkout's skill directory>` in front. The repo's own copy has no skill history, so without it `stamp.json` records no `skill` commit and the install prints `skill: unknown`.

To run every agent on Claude Code instead of Pi, add `--coding-agent claude_code`. The installer then stamps `templates/sssf.config.claude_code.yaml` as the roster. It has the same prompts, purposes, and `writes` boundaries as the Pi roster, with Anthropic models and no pi extensions. It uses your `claude` subscription login, so `.env` needs no API key. Check the login with `claude auth status`.

To run every agent on Pi signed into a ChatGPT subscription, add `--chatgpt`. The installer stamps `templates/sssf.config.chatgpt.yaml`: the same prompts, purposes, and `writes` as the Pi roster, with gpt-6.1-sol for the planner, builders, and reviewers, and gpt-6.1-terra for the scout and documenter. Sign Pi in first with `pi`, then `/login`, then `openai`, and check it with `pi auth check --provider openai --json`, which must report `"authType": "oauth"`. Set `SSSF_PI_AUTH=subscription` in `.env` so a lapsed sign-in fails the run instead of billing an API key.

## What gets stamped

`install.py` copies `templates/` into the cwd:

| Stamped | From | Tracked? |
|---|---|---|
| `adws/adw_sssf_config/sssf.config.yaml` | `templates/sssf.config.yaml` | yes — the agent roster |
| `adws/adw_sssf_config/repo.yaml` | `templates/repo.yaml` | yes — the repo's own rules, such as fast-forward-only merges. Yours to edit |
| `.env.sample` | `templates/env.sample` | yes |
| `adws/adw_*.py` | `templates/adws/` | yes — the 14 starter workflows, and `adw_kill`, `adw_worktree` and `adw_sandbox_usage` for managing runs |
| `adws/adw_modules/` | `templates/adws/adw_modules/` | yes — all low-level logic |
| `adws/adw_data/prompt_engineering/<agent>/`, one dir for each of the 8 starter agents and the trial `referee` | `templates/prompt_engineering/` | yes — **the user-owned home for prompts** |
| `adws/adw_data/harness_engineering/` | `templates/harness_engineering/` | yes — **the user-owned home for pi extensions** |
| `justfile` | `templates/justfile` | yes — starter recipes: `just demo`, the workflows, the trace reads, `just obs` |
| `.githooks/pre-push` | `templates/githooks/` | yes — the secret scan before every push. Off until the engineer turns it on: see [Secret scan before every push](#secret-scan-before-every-push) |
| `adws/adw_data/sessions/`, `adws/adw_data/sssf.db` | created at runtime | no — gitignored |

The two `*_engineering` dirs mirror the two config keys of the same name: `prompt_engineering` is what an agent is told, `harness_engineering` is what its harness can do. Both are yours the moment they are stamped. Edit them in `adws/adw_data/`, never back inside the skill.

`harness_engineering/` ships with `subagents.ts` — the pi extension backing `subagent_create` / `_continue` / `_list` / `_remove`, wired to the planner and scout in the starter roster.

## Idempotency

Re-running is safe. `install.py` skips **every** file that already exists — your config, your prompts, and previously stamped code alike — and reports what it skipped, so a second run doubles as a drift check. `--force` overwrites ALL existing stamped files, including `sssf.config.yaml`, `quality.py`, and `prompt_engineering/`. To bring a repo up to a newer skill, use `--update` instead (next section).

A repo's own `justfile`, `.env.sample` or `.githooks/pre-push`, there before SSSF, is skipped and recorded as kept, the same as `--update --keep` would record it. The first update leaves it alone, and it is only flagged again when the skill's template changes. A repo with its own justfile can load SSSF's recipes as a module instead: add `mod sssf 'sssf.just'` to it, then install or update. SSSF stamps its recipes into `sssf.just`, records it, and keeps it current on each update, like the rest of its code. Run them as `just sssf demo`, `just sssf sessions`, and so on. The repo's own recipes stay in its justfile, which SSSF never records or changes.

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
| is SSSF code the skill no longer ships, and still matches its recorded hash | removes it (`-`, under `removed`). Changed, or not a regular file: leaves it and lists it |

A malformed `stamp.json` makes `--update` refuse before it writes anything, naming each bad record. Restore the file with `git checkout -- adws/adw_sssf_config/stamp.json`, read `git log` on it, and run the update again.

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

1. Resolve every `!` file with `--take` or `--keep`, then check with `uv run .claude/skills/sssf/scripts/install.py --update` until `to review` is 0. `just sssf-update` refuses here, since the files you resolved leave the tree dirty.
2. Read `git status` and `git diff`. Expect the files the update listed, `stamp.json`, and the skill copy under `.claude/skills/sssf/`. A file you did not expect is a question for the hub, not something to commit.
3. If the update listed `repo.yaml`, bring in its new settings. For a `!` `repo.yaml`, copy in the skill's change first, keeping the repo's own values, then `--keep` it. Then decide each setting the repo's copy didn't have before, and give each a one-line comment saying why. These edits go into the update commit.
   - `max_worktree_runs`: how many worktree runs go at once. The default is 2. Is `quality.py` safe to run twice at the same moment? Two runs check in two folders at once, so a check that binds a fixed port, writes a fixed temp path, or uses one shared test database breaks the other run's checks. If it isn't safe, write 1.
   - `worktree_setup`: what a fresh checkout needs before the checks can run. A worktree run's folder is a new checkout, without the untracked dependencies yours has, such as `node_modules`. List the commands that install them, the way the README does, for example `cd web && bun install --frozen-lockfile`. They must only create gitignored files. A Python repo on uv needs none: leave it `[]`.
   - `export_checks`: in a fork with an `upstream` remote, the repo's own checks, which `just export` runs on the branch it builds for upstream. That branch has no `quality.py`, so list the commands `quality.py` runs, or upstream's CI. In any other repo, leave it `[]`.
   - `max_live_cost`: what every live run may spend together. It's off by default. Set nothing, and tell the hub it arrived; onboarding is where the engineer chooses it.
   - `max_daily_cost`: what the repo's runs may spend in the last 24 hours. The same: off by default, set nothing, and tell the hub it arrived.
   - `max_silence_minutes`: how long an agent may write nothing before it and the run are stopped. On at 15 by default; set nothing. A repo whose agents run quiet commands longer than that, such as a long build with no output, raises it.
   - The machine's own `max_daily_cost`, in `~/.config/sssf/limits.yaml`, covers every repo together. It isn't in `repo.yaml`, and an update never writes it. Mention it in one line to an engineer who runs more than one repo unattended.
   - Trials (`just trial`) take one worktree slot per roster, so a trial of three rosters needs `max_worktree_runs: 3`. Don't raise it for that alone; the engineer raises it when they want a three-roster trial. A second roster, such as ChatGPT, arrives only through `install.py --add-roster <name>`, never through an update, so an update adds no roster file.
   - `agent_rules`: rules every agent gets at the end of its system prompt. A plain string asks; a rule with `forbid` regexes also stops the run when an agent's shell command matches (`references/config.md`). It arrives empty. Leave it `[]`: the rules are the engineer's to choose. If the repo has a rule every agent should follow, such as one in `CLAUDE.local.md` (agents never read it), suggest it in your report.
   - A setting not listed here: read its comment in the skill's `repo.yaml`, and keep the default unless the repo needs otherwise.
   - If the update added or flagged `.githooks/pre-push`, read [Secret scan before every push](#secret-scan-before-every-push). For a `!` hook, the repo had its own: diff the two. The skill's scans with gitleaks and refuses a push from an agent. If the repo's does no more than that, `--take` it; if it does more, copy the skill's checks into it and `--keep` it. The update's last lines say whether the scan is on. If it isn't, pass that line to the engineer in your report; don't set `core.hooksPath` yourself.
4. If the update changed `adws/adw_modules/rules.py`, `sssf_refuse.py` or `sssf_rules.ts`, and the repo's `agent_rules` has any `forbid` rule, retest those rules before you commit. These files decide what a rule catches, and a change can weaken a rule without any test in the skill knowing. Two project sessions caught a weakened rule this way, before committing it. Write the commands to try: for each `forbid` rule, a few forms it must refuse (plain, after `&&` or `;`, on the second line of a command, piped from a heredoc into `bash`, inside `bash -c`) and a few it must let through (a near-miss word, such as `pip` for a `pi` rule, or the command's read-only form). Run them through both checks, from the repo root. The snippet only calls the check functions, so nothing in the list is run. Don't test by running a command through `adws/adw_modules/sssf_refuse.py` itself: it runs every command it allows, so a plan case writes `plan.md` into the repo:

   ```bash
   uv run --with pyyaml --with pydantic python3 - <<'EOF'
   import sys, yaml
   sys.path.insert(0, "adws")
   from adw_modules import rules, sssf_refuse
   raw = yaml.safe_load(open("adws/adw_sssf_config/repo.yaml")).get("agent_rules") or []
   rs = [rules.AgentRule(**r) for r in raw if isinstance(r, dict)]
   cases = [  # (command, should be refused)
       ("git push origin main", True),
       ("git pull --ff-only", False),
   ]
   for command, want in cases:
       hit = rules.breach(rs, command)   # the rule it breaks, or None
       refused = sssf_refuse.refusal(command, rules.refusing(rs)) is not None
       # only on_match: refuse rules are refused before they run; stop rules stop the run
       ok = (hit is not None) == want and refused == (want and hit[0].on_match == "refuse")
       print("ok " if ok else "BAD", hit is not None, refused, repr(command))
   EOF
   ```

   `rules.breach` is the check on every agent's stream, and `refusal` is the one that refuses a command before it runs. On a pi roster, the extension `sssf_rules.ts` compiles the same patterns with the `m` flag; run the cases through it under `bun` too if the update changed it. Every line must say `ok`. If a command the repo's rules refused before the update now passes, the update weakened a rule: don't commit. Put the tree back (`git checkout -- .`, and remove any file the update added, by name), and send the hub the failing commands. A rule that was already weak before the update, such as a pattern anchored with `^` that misses the second line, is the repo's to fix in `repo.yaml`, in the update commit; say so in your report.
5. Commit the update on its own: `git add -A && git commit -m "Update SSSF to skill <sha>"`, with the sha the update printed. A file the update just added (`+`) that you edit straight away, such as `repo.yaml`, can go into this commit with your edit, since it never existed without it.
6. Run `uv run adws/adw_quality.py "check after the SSSF update"`. A block reported as `not configured` is still on its placeholder, and checks nothing until you wire it in `quality.py`.
   To run the skill's own tests too, such as the guard tests after a guard change, run this from `.claude/skills/sssf`: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`. The tests import `adw_modules`, which needs every one of those packages.
   Quality never starts an agent, so it cannot tell you the agents can still sign in. On a Claude Code roster, check that too, with the agents' own environment: `just login-check` (`just sssf login-check` with `mod sssf`). It runs the same check a chain runs before its first agent.
7. Report the commit, the update's counts, the `max_worktree_runs` and `worktree_setup` you chose, anything flagged, the rule retest from step 4 if you ran one, and the quality result to the hub: the Claude session that works in the SSSF skill repo and sent the update ([onboard.md](onboard.md), "Two sessions"). With no hub session, report to the engineer.

## Run the operator session in a container

On the host, Claude Code's auto mode blocks the command that launches a chain, so the engineer approves each launch. `--container` stamps a Podman setup that runs the operator session with `--dangerously-skip-permissions` instead. Inside it, the session can reach only the repo and `~/.config/sssf`, both mounted at their host paths, so the trace and `just obs-all` on the host see every run. It covers the operator session and every agent it starts. It does not give each run its own branch: a run still commits to the branch that is checked out.

It needs Podman. A pi roster also needs the host signed in to pi (`pi`, then `/login`, `openai`): the host keeps the sign-in, and each pi agent gets a one-hour token from it. A session needs Podman with pasta, the rootless default from Podman 5. Obs events from the container show on the host dashboard, because `127.0.0.1:43190` is forwarded. A pi extension must be inside the repo. If a run stops on a stored ChatGPT login, delete the `openai` entry from `~/.pi/agent/auth.json` inside the container.

```bash
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code --container   # new install
uv run .claude/skills/sssf/scripts/install.py --container                              # add it to an installed repo
```

| File | What it does |
|---|---|
| `container/Containerfile` | The image: Ubuntu, git, sqlite3, uv, just, Claude Code, node 24 and pi 1.4.0 (both pinned), a C compiler with Python headers, and tzdata. **The repo's to edit**: add the tools `quality.py` runs, such as bun. An update keeps your edits, like `quality.py` |
| `container/build.sh` | Builds the image as `localhost/sssf-operator-<repo>`. Run it again after editing the Containerfile |
| `container/pi_token_broker.py` | `run.sh` runs it on the host. It hands each pi agent a one-hour ChatGPT token through a socket |
| `container/run.sh` | Starts the session. Builds the image on first use, and rebuilds it when the Containerfile changed |

Keep `container/` out of the repo's package builds, as with `adws/` and `.claude/`. A Python sdist includes it otherwise.

The engineer sets it up once, in their own terminal:

1. Make a token, unless `~/.config/sssf-container/token.env` already exists. Every repo shares that file, and a token lasts about a year. To make one: `claude setup-token`. It opens a browser sign-in and prints a token that starts with `sk-ant-oat`. Save it without echoing it:

   ```bash
   mkdir -p ~/.config/sssf-container && chmod 700 ~/.config/sssf-container
   read -rs -p "Paste token, then Enter: " T && printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\n' "$T" > ~/.config/sssf-container/token.env && unset T && chmod 600 ~/.config/sssf-container/token.env
   ```

   `SSSF_CONTAINER_TOKEN` points `run.sh` at another file. The token signs in to the subscription: keep it out of the repo and out of chat.
2. Start the session: `container/run.sh`, in a terminal of its own, from a login shell that sets `XDG_RUNTIME_DIR` (rootless Podman needs it, and the pi token broker's socket lives there; `run.sh` stops if it's unset). It runs an interactive Claude Code session, so it can't run from a `!` command in another session, which has no terminal. It skips Claude's first-run login menu, since the token already signs it in. Answer yes to trusting the folder, and accept the bypass-permissions warning. A home volume, `sssf-operator-home`, remembers both.
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
1. **Env** — `cp .env.sample .env`. A pi roster on API keys needs a key for each provider it names: the starter pi roster needs `OPENROUTER_API_KEY`, `FIREWORKS_API_KEY` and `OPENAI_API_KEY`, and `.env.sample` says which model needs which. A `--chatgpt` roster needs none; set `SSSF_PI_AUTH=subscription`. (Claude Code agents need no key: they run on the operator's `claude` login, and `agent_cc` strips `ANTHROPIC_API_KEY` from them unless `SSSF_CLAUDE_AUTH=api_key`.)
2. **Pi is installed and on PATH** — `pi --version`. Set `PI_PATH` in `.env` if it is not.
3. **The models resolve** — every `provider/model-id` in the roster must appear in `pi --list-models`. The default `google/gemini-3.6-flash` resolves through pi's built-in openrouter catalog once `OPENROUTER_API_KEY` is set, and a provider with no key lists no models. See `references/config.md` for model resolution.
4. **Gitignore** — `install.py` appends its entries for you, among them `adws/adw_data/sessions/`, `adws/adw_data/sssf.db*`, `adws/adw_data/worktrees/` and `.env`; confirm they landed. They're runtime state or secrets, and must never be committed.
5. **Git repo** — ADWs that end in a commit phase call `git_helper.commit_all`, which raises if the cwd is not a git repository. Run `git init` and make a first commit before using `adw_plan_build.py`, `adw_plan_build_test.py`, or `adw_simple_sdlc.py`. `adw_document.py` needs one too: it measures the change with `git diff` against a base ref (`main` by default, `--base` to override). Then ask the engineer to turn on the secret scan with the line `install.py` printed: [Secret scan before every push](#secret-scan-before-every-push).
6. **Keep SSSF out of the project's own tools.** The install adds about 150 files of Python, TypeScript, and Vue under `adws/` and `.claude/`, and they don't follow your project's rules. A linter, formatter, or type checker that runs on the whole repo then fails on SSSF's code, not yours. In one project, CI's `ruff check .` and `ruff format --check .` failed with 434 errors and 64 files, all in those two folders. A package build can also take them in: its first `uv build` put 148 SSSF files into the sdist. `quality.py` names your paths, so the chain passes, and CI fails later. Exclude both folders where your tools read the whole repo.

   Exclude `specs/` and `app_docs/` from formatters and linters too. They are SSSF's records of each run, not code, and ruff 0.16 also formats Python blocks inside Markdown. In one project's run, the committed spec failed `ruff format --check .`, and the builder's `ruff format .` rewrote it. Package builds can leave these two folders alone; they are small and harmless.

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

   Package builds should leave out `.githooks/` too: `"/.githooks"` in the hatchling exclude, `prune .githooks` in `MANIFEST.in`.

   `install.py` does not edit these files: they belong to the project. A wheel limited to your package is already clean. The sdist still carries a few small SSSF files at the top level (`justfile`, `.env.sample`, `specs/`, `app_docs/`). They are harmless, and you can exclude them the same way.

7. **Parallel runs** — Is `quality.py` safe to run twice at the same moment? Two worktree runs check in two folders at once, so a check that binds a fixed port, writes a fixed temp path, or uses one shared test database breaks the other run's checks. If it isn't, set `max_worktree_runs: 1` in `adws/adw_sssf_config/repo.yaml`.
8. **Worktree setup** — What does a fresh checkout need before the checks can run? A worktree run's folder is a new checkout, so it has none of the untracked dependencies yours has, such as `node_modules`. List the commands that install them under `worktree_setup` in `adws/adw_sssf_config/repo.yaml`, the way the README installs them, for example `cd web && bun install --frozen-lockfile`. They must only create gitignored files. A Python repo on uv needs none.
9. **Export checks** — Is the repo a fork with an `upstream` remote (`git remote`)? If so, `just export` builds the branches that go upstream, with no SSSF files and so no `quality.py`. List the repo's own checks under `export_checks` in `adws/adw_sssf_config/repo.yaml`: the commands `quality.py` runs, or upstream's CI. Exports keep each commit's author, so ask the engineer which email the repo's commits should carry, and have them set it with `git config user.email <address>` in the repo. Otherwise leave `export_checks` as `[]`.
10. **Shared spend** — What may every live run in this repo spend together? Each run's `max_run_cost` holds for that run alone, so two runs at once can spend up to twice it. Ask the engineer, suggesting `max_run_cost` times 1.5, and write their answer as `max_live_cost` in `adws/adw_sssf_config/repo.yaml`, or leave it out to keep it off. Each live run can still overshoot it by one call.
11. **Daily spend** — What may this repo's runs spend in a day? `max_live_cost` holds only runs that are live, so runs one after another can each spend a full `max_run_cost`. Ask the engineer, suggesting `max_run_cost` times 4, and write their answer as `max_daily_cost` in `adws/adw_sssf_config/repo.yaml`, or leave it out to keep it off. It covers the last 24 hours, rolling, finished runs included. If they run more than one repo unattended, say in one line that `max_daily_cost` in `~/.config/sssf/limits.yaml` caps every repo on the machine together; they set it by hand.
12. **Private repo** — Is the repo private? Check with `gh repo view --json visibility` when `gh` is signed in, and ask the engineer otherwise. If it is, leave `sandbox: none` in `adws/adw_sssf_config/repo.yaml` and tell the engineer in one line that `--sandbox vercel` or `--sandbox e2b` uploads the repo's code to that company, so a VM run happens only when they ask for one. Agents send code to their model provider on any run, here or in a VM; that's not new. Then say that `.git/sssf-private-names`, one name per line, makes the pre-push hook refuse any push that names a private project, for a public repo that mustn't mention this one: [Secret scan before every push](#secret-scan-before-every-push).
13. **Smoke test** — `just demo` runs two cheap read-only workflows back to back, or run the smallest ADW directly:

```bash
just demo                                                    # both, end to end
uv run adws/adw_prompt.py "reply with a one-line summary of this repo"   # the raw form
```

Green means the whole path works: config validated, session minted, Pi ran, envelope parsed, events landed in `adws/adw_data/sssf.db`. Verify the trace exists before trusting anything larger:

```bash
sqlite3 adws/adw_data/sssf.db "select adw_id, status from sessions order by started_at desc limit 1;"
```

If the smoke test fails, fix it before composing chains — every multi-agent ADW rides on this exact path.

## Secret scan before every push

`.githooks/pre-push` runs [gitleaks](https://github.com/gitleaks/gitleaks) over the commits a push would send, and refuses the push on a find. For a branch the remote already has, it scans the commits past the remote's copy; for a new branch, the commits on none of the remote's branches. Each find prints redacted, with its rule, file and commit. It also refuses when `SSSF_ADW_ID` is set, so an SSSF agent can't push, and when gitleaks isn't installed, so a missing scanner can't let a push through unscanned.

- **Turning it on is the engineer's step:** `git config core.hooksPath .githooks`, once per clone. Git doesn't run a repo's tracked hooks until the clone opts in. Don't run it yourself, even when asked: it changes what git runs on every push, and an agent's permission check refuses it as persistence. Give the engineer the line `install.py` prints.
- **It switches off `.git/hooks`.** With `core.hooksPath` set, git stops reading `.git/hooks`. `install.py` names any hook there; merge what it does into `.githooks/` before the switch. In one project an untracked `.git/hooks/pre-push` refused agent pushes, which is why the skill's hook does that too.
- **gitleaks** 8.30.1 or later, on `PATH`. Install it from its GitHub release and check the release's SHA-256 against its checksums file.
- **Check it** by running the hook as git does, never with a real push: in a scratch clone, commit a fake token (`ghp_` and 36 random letters, never a real secret) and run `echo "refs/heads/main $(git rev-parse HEAD) refs/heads/main $(git rev-parse origin/main)" | .githooks/pre-push origin`. It should exit 1 and name the file. A clean commit exits 0.
- **Private names.** If `.git/sssf-private-names` lists names, one per line with `#` for comments, the hook also refuses a push whose commits name one, in a message or a change, ignoring case, and names the commit. It checks only the lines a commit adds, so a commit that takes a name out passes. The file sits in `.git`, so it's never committed, and it's the engineer's: write it only when they ask. It suits a public repo whose engineer also works on repos they don't want named, such as an SSSF fork recording its installs. SSSF's git-directory guard puts the file back and stops the run if it changes during an agent call, the engineer's own edit included, so edit it between runs.
- **`git push --no-verify`** skips it. That's the engineer's call, knowing nothing was scanned.
- **Keep `.githooks/` out of package builds**, beside `adws/` and `.claude/` (step 6 of the checklist).
