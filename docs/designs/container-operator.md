# Design: run the operator session in a container

Status: built and shipped as an opt-in install option (`install.py --container`, fdbeb08). In real use in a project since 2026-10-30. This file tracks the experiment: what was decided, what was tested, and what's still open. Add to the log as it moves.

## Why

The operator session is the Claude Code session that writes an ADW prompt and launches `uv run adws/adw_<chain>.py`. In auto mode, the permission classifier blocks those launches. The two ways out on the host are `!` launches by hand or an allow rule, and bypass mode on the host is not recommended.

Inside a container, bypass mode is the standard advice: the session can only touch what the container mounts. So the operator runs there with no prompts, and the host stays out of reach.

The agents a run starts were never the problem. SSSF's Claude Code runner already passes `--permission-mode bypassPermissions`, and Pi agents never ask. What they lack is isolation, which the container also gives them, because they run inside it.

## What it covers, and what it doesn't

SSSF's README lists what the core deliberately leaves out: "no sandbox, no branch per run, no merge step, no cloud, and no human-in-the-loop approval phase."

The container covers the sandbox, and only the sandbox. Runs still commit to whatever branch is checked out, and nothing merges a run's work back.

[Sandcastle](https://github.com/mattpocock/sandcastle), read from the fork at `~/github/sandcastle`, does all three: a container, a git worktree and branch per run, and a merge back. It's a TypeScript library, and SSSF is Python, so using it directly would mean calling Node for every agent and rewiring session resume, the trace, and obs pool tagging. We took the idea for the operator session and left the library.

## Decisions

1. **Opt-in at setup, outside the core.** `install.py --container` stamps `container/Containerfile`, `build.sh`, and `run.sh`. Without the flag nothing is stamped, and no ADW knows about the container. `onboard.md` asks the question in step 2.
2. **pi rosters sign in through a host token broker.** Until 2026-11-05 this was "Claude Code rosters only": a pi roster got a parser error, since the image had no pi and no ChatGPT sign-in went in. The section "pi in the container" below has the decisions that replaced it.
3. **Podman, rootless, `--userns=keep-id`.** Docker isn't installed in the WSL distro. Files the container writes are owned by the engineer on the host.
4. **The repo and `~/.config/sssf` are mounted at their host paths.** The trace db, run folders, and registry are the host's own, so the host dashboard shows container runs with no extra wiring.
5. **One image per repo, one shared home volume.** The image is `localhost/sssf-operator-<repo>`. The `sssf-operator-home` volume keeps Claude Code's settings between sessions.
6. **Subscription auth through a long-lived token.** `claude setup-token` writes `CLAUDE_CODE_OAUTH_TOKEN` to `~/.config/sssf-container/token.env`, outside every repo. No API key. The token lasts about a year, so a new repo reuses it.
7. **Kept current by updates.** `has_container(record)` keeps the stamped files on `--update` once a repo has them.

## Problems found and fixed

- **Login menu on first start.** Claude Code showed its sign-in menu even with the token set. `run.sh` now sets `hasCompletedOnboarding` in `~/.claude.json`.
- **Agents not logged in (run a03e7b00).** A Claude Code session doesn't pass its `CLAUDE_CODE_OAUTH_TOKEN` to the shell commands it runs, so the chain's agents had no login. `run.sh` exports it through `CLAUDE_ENV_FILE=/tmp/sssf-auth.sh`. The same failure led to SSSF's login check (ab2a35c): a chain now stops before its first Claude Code agent when there's no login, and `just login-check` tests it by hand.
- **Runs missing from the host dashboard.** Fixed by mounting the repo and `~/.config/sssf` at their host paths (decision 4).
- **uv's environment clashing with the host's.** `run.sh` sets `UV_PROJECT_ENVIRONMENT` per repo, and sets the `GIT_*` identity from the repo's git config.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-10-30 | sssf-sandbox `exp/container-operator` | hand-made container, run a03e7b00 | failed: agents not logged in |
| 2026-10-30 | sssf-sandbox `exp/container-operator` | run 1d56f792 after the `CLAUDE_ENV_FILE` fix | success |
| 2026-10-30 | hub | `--container` install option, fdbeb08 | shipped |
| 2026-10-30 | sssf-sandbox `exp/container-option` | reinstalled through `--container`, existing token, run 888c6d27 | success, visible in the host trace and dashboard |
| 2026-10-30 | an unlisted project | `--container` setup through the skill, aa2140b, quality 7f07c83d | set up; token and login checks passed inside |
| 2026-10-30 | an unlisted project | first real run from the container, 47caf9b9 (`--brief` option) | success after 3 failed test rounds: no C compiler to build `brotli`, and no tzdata, so named time zones fell back to UTC. The builder rewrote two existing tests to POSIX `TZ` strings to pass. Launch, host trace and dashboard, commit author, and file ownership were all right |
| 2026-10-30 | hub | image gets build-essential, python3-dev and tzdata; container doc gaps closed, bbcff04 | shipped |
| 2026-10-30 | an unlisted project | rebuilt image, run 650cbab9 (`--brief` shows progress) | success: all 5 checks passed on the first round, 10 phases, about 3 minutes. Author and file ownership right |
| 2026-10-30 | sssf-sandbox `master` | worktree runs from the container: 84f61760, then d8fc85aa | the first died on a closed output pipe, not the container; the second merged. See [branch-per-run.md](branch-per-run.md) |
| 2026-11-05 | sssf-sandbox | pi in the container, first try | stopped before any agent: the roster's model check ran the container's pi, which has no login, so it listed no openai models. Fixed through the guard chain in 99c6f4fa, which signs the check in through the broker. The Claude Code run was refused by four runs from a VM proof that still read `running`; `just kill` on the host closed them |
| 2026-11-05 | sssf-sandbox | pi in the container: build_test with the extension roster (211bfc7f, $0.016) and the Claude Code roster (abd8bd30, $0.019) | both success, from hostname sssf-operator-sssf-sandbox. pi's builder ran openai/gpt-6.1-sol on the broker's token (the container's `auth.json` holds no login); `vm_marker` returned SSSF-VM-EXT-7f3a from the file it imports; `echo SSSF-REFUSE-PROBE` got "SSSF refused this command"; the `OBS_` names were NAME, POOL and TAG, with no `OBS_DISABLE`. The host's pi still minted a token afterward |
| 2026-11-05 | pi-agent-observability | pi in the container with the obs extension: build_test (a97a2d4b, $0.014) | success, from hostname sssf-operator-pi-agent-observability. pi's builder ran openai/gpt-6.1-sol on the broker's token; the host's obs recorded its session in pool sssf-a97a2d4b with 33 events, through run.sh's pasta forward. The image adds bun for that repo's quality.py |

## Follow-ups

Open items for this design are in [open.md](../open.md). This section keeps the follow-ups that closed.

- **First real use: done.** A project runs from the container. Its first run exposed the missing compiler and tzdata, and the doc gaps its setup found are closed. Its second run, after the fix, passed every check on the first round.
- **Branch per run and a merge back: done.** Built to [branch-per-run.md](branch-per-run.md) on 2026-10-30, proven in sssf-sandbox on the host and in this container (run d8fc85aa), and rolled out to both projects. The sandbox seam it left open is now built; see "The sandbox seam" below.
- **Sandbox branches: done.** sssf-sandbox is on its trunk, `master`, with no other branches.
- **Parallel runs in the container: done.** Built to [parallel-worktree-runs.md](parallel-worktree-runs.md). Each worktree run in the container gets its own project environment, removed on merge or discard, and `run.sh` gives the container a fixed hostname, so the trace tells its processes from the host's. Proven in sssf-sandbox (pr3conso and pr3title together, then pr4contr beside a host run). Each environment's editable install points at its own worktree, checked in this image with a scratch package.
- **The sandbox seam: built.** [remote-sandboxes.md](remote-sandboxes.md), settled on 2026-11-02, built it against Vercel Sandbox and E2B the same week. `--sandbox vercel` and `--sandbox e2b` run beside `--container`, and that doc keeps what is still open.
- **pi in the container: built and proven.** Settled and built on 2026-11-05 (runs 28541d50, 3ca0a481 and 99c6f4fa; see "pi in the container" below) and proven the same day: in sssf-sandbox for the broker, extensions and refusal, and in pi-agent-observability for obs.

### pi in the container

Settled in a grilling session on 2026-11-05, after pi extensions in the VM ([remote-sandboxes.md](remote-sandboxes.md), decisions 20 to 27). The container differs from a VM in one way that drives every choice here: the operator session and every agent run inside it, with the repo mounted at its host path, and no SSSF process runs on the host beside them. In a VM run, a host process gives each pi agent a ChatGPT access token (`agent_pi.vm_token`). In the container, nothing on the host is there to do it, so the host gets a small helper that is.

8. **A host token broker signs pi in.** The ChatGPT refresh token stays on the host, as in a VM run. `run.sh` starts `container/pi_token_broker.py`, a stdlib Python script, in the background before `podman run`, and stops it with a `trap` when the session ends. `run.sh` first removes a socket left at the path by an earlier session, and waits until a connection to the new broker succeeds, exiting 1 if none does. It listens on a Unix socket, `$XDG_RUNTIME_DIR/sssf-pi-<repo>.sock`, mode 600, mounted into the container at `/run/sssf/pi.sock`, and `run.sh` sets `SSSF_PI_TOKEN_SOCKET` to that path. A client connects; the broker runs `pi auth print-bearer-token --provider openai --min-expiry 35m` and writes back the token or one error line, and never logs the token. For each pi agent call, `agent_pi` asks the socket when `SSSF_PI_TOKEN_SOCKET` is set and passes the token as `OPENAI_API_KEY`, as a VM run does. The roster's model check (`pi --list-models`) and the trial's openai sign-in check ask it too. Agents can't change the operator's environment, so they can't point it elsewhere. Rejected: mounting the host's `~/.pi/agent`, which hands bypass-mode agents the 30-day refresh token and every other credential pi stores; and a separate `/login` in the container, which is a session `/logout` can't revoke, shared by every repo through the home volume.
9. **Any process in the container may ask the socket.** Everything there runs as one user, so a secret that kept agents out would be one they could read. An agent gets what every pi agent already has, an access token that expires within the hour. Known limit: while a session is open, an agent can keep minting new one-hour tokens.
10. **A ChatGPT login stored in the container stops the run.** pi prefers a stored credential to `OPENAI_API_KEY`, so a `/login` once run in the container, kept in the shared home volume, would quietly win over the broker. SSSF checks the container's `~/.pi/agent/auth.json` (or `$PI_CODING_AGENT_DIR/auth.json`, where pi reads it then) by key name only; an `openai` entry stops the run before its first pi agent and says to remove it. Anything there but a regular file also stops the run, and the check never opens one. The same check reads `models.json` in that dir and stops on an openai `apiKey` or `headers`, because pi resolves a configured key ahead of `OPENAI_API_KEY`.
11. **The broker starts whether or not the host can sign pi in.** A Claude Code roster never depends on it. A pi agent with no host sign-in, or no pi on the host, fails at config validation, before any agent, with the broker's error, worded as a VM run's is: sign in with `pi`, then /login. A host credential that is an API key (`sk-`) is refused, as `vm_token` refuses it.
12. **Every image has node and pi, pinned.** Node major 24, as on the host, and pi at an exact version, 1.4.0 to start, the tool check's minimum. About 150MB per image, so switching a repo's roster never needs a Containerfile edit.
13. **obs works as on the host.** `run.sh` forwards the container's `127.0.0.1:43190` to the host's with pasta's `-T 43190`, so the extension's default URL reaches the host's obs server and its events show on the host dashboard. There is no `OBS_DISABLE` and `OBS_AUTH_TOKEN` isn't held back: unlike in a VM (decision 23 there), the repo and its `.env` are already mounted, so holding the token back would keep nothing from an agent.
14. **Extensions load from the mounted repo, and only from inside it.** The repo is at its host path, so a roster's `harness_engineering` paths resolve as on the host, uncommitted files included. A path outside the repo, such as `~/.pi/agent/extensions/x.ts`, doesn't exist in the container: a check at launch stops the run with a message naming the agent and the path, instead of pi failing at the first call.
15. **`run.sh` rebuilds a stale image.** `build.sh` labels the image with the Containerfile's hash, and `run.sh` rebuilds when the label doesn't match. Otherwise an image built before decision 12 would fail its first pi agent with "pi: not found", and a repo's own Containerfile edits would still need a manual `build.sh`.
16. **Through the guard chain, then a real proof.** `agent_pi.py` is guard code. The proof, in sssf-sandbox from the container: the marker extension loads; the refuse rule answers "SSSF refused this command"; an obs event from the run shows on the host dashboard; the host's own `pi` is still signed in afterward; and a Claude Code roster runs as before.

`cookbooks/install.md` says that a pi roster in the container needs the host signed in to pi.

### What a second sandbox means

The seam is [branch-per-run](branch-per-run.md) decision 5: the code that runs a session in a sandbox stays apart from the branch and merge code, behind the contract "run this operator session, or this command, in this folder, with these mounts, and report how it ended." No code exists for it. Built around one implementation, the interface would guess at what the second needs.

Sandboxes come in two kinds, as sandcastle's providers show (`~/github/sandcastle/src/sandboxes`):

- **Bind-mount** (Docker, Podman): the repo folder is shared with the host. This container is one. Decision 4 mounts the repo and `~/.config/sssf` at their host paths, so the trace, run folders, worktrees, and registry are the host's own files. The dashboard, `max_live_cost`, `max_daily_cost`, the launch lock, and `just merge` all work for container runs with no extra wiring. A second bind-mount sandbox fits as it stands.
- **Isolated** (Vercel, Daytona, E2B): the sandbox is a remote VM. Code is copied in and results pulled out. A run there can't write the host's trace, so it needs a way to keep the trace and the caps current, and a way to bring its commits back to `sssf/<adw_id>` before `just merge`. That, not the interface, is the work.

Nothing asks for a second sandbox yet. The needs that would are running unattended while this machine is off or busy, or more parallel runs than one machine holds, both pointing at an isolated provider, or a stronger boundary than rootless Podman for untrusted code, which could stay local. The first question to settle then: does the trace stay on the host, or travel with the run?

### disler's E2B skill

disler, who wrote SSSF, also wrote agent-sandbox-skill, read from the fork at `~/github/agent-sandbox-skill` (ac460ee). It's an isolated sandbox on [E2B](https://e2b.dev/), built for a different job:

- **The agent stays on the host.** Claude Code, Gemini CLI, or Codex drive a remote E2B VM through a Python CLI, `sbx`: `exec` streams a command's output, `files` reads, writes, uploads, and downloads, and `sandbox` creates, pauses, extends, and kills. Only the commands and files are remote.
- **Greenfield apps, not a repo's changes.** Its workflow, `plan-build-host-test`, plans locally, builds a Vue, FastAPI, and SQLite app inside a pre-built template, exposes it on a public URL, and browser-tests it. An existing repo comes in by `git clone` inside the VM; work comes back by `download-dir` into `temp/<workflow_id>/`, leaving `.git` out. There is no branch, commit, or merge back.
- **Prompts own the sequence.** Slash-command prompts chain the steps, and the agent keeps the sandbox ID in its context. There is no trace, cost cap, or gate: the opposite of SSSF's rule that code owns sequencing, retries, and acceptance.
- **Its own costs.** An `E2B_API_KEY` in `.env`, and $0.13 to $0.44 an hour per VM by template size, for up to its default 12-hour timeout, on top of model spend.

What it suggests for SSSF, if an isolated sandbox is ever needed: keep the ADW on the host and send only agents and checks to the VM. The trace, the caps, the launch lock, and the dashboard would then stay as they are, and the hard part shrinks to moving the run's branch in and its commits out, for example as a git bundle. Two costs would remain:

- **Claude Code inside the VM.** Agents that edit files with their own tools need Claude Code there, and its token on a third party's machine. Agents left on the host, editing through `sbx`, would lose those tools and the tree check around each agent.
- **Spend the caps can't see.** The VM's hourly cost is outside every cap, and the trace doesn't count it.

Worth reusing: `sbx exec`'s line-by-line streaming, its pause and extend calls, and `build_template.py`, which builds an image per stack, as `container/Containerfile` does per repo. None of this changes the seam's status: it waits for a need.
