# Design: a local OpenShell sandbox

Status: built 2026-11-07, and the proof run (step 4) passed 2026-11-08. The engineer accepted decisions 1 to 5 as proposed on 2026-11-07.

## Why

Most of SSSF's guard code exists because agents run on the engineer's machine, with its network and its files ([concepts.md](../concepts.md#guards)). Each pattern rule against `git push` or a billing recipe has been patched after a miss, and the open items left are ones a rule can't fix: an agent can forge events into its own output stream ([open.md](../open.md)). The fix named there is isolation, "a VM, or agents under their own user". Vercel and E2B give that today, but they're remote, metered and upload the code.

NVIDIA OpenShell runs an agent in a local microVM whose network is closed unless a policy opens it, per program ([the research](../research/openshell.md)). A push fails with no route, however the command is written.

The goal: `--sandbox openshell`, a third provider beside `vercel` and `e2b`, local and free, for Claude Code rosters first.

## What was found

From [the test](../research/openshell.md#the-test) on 2026-11-07, on this WSL2 machine:

- **Only the microVM driver runs here.** The Podman and Docker drivers die at their seccomp probe, because WSL's init holds the one seccomp listener a process chain may have. The microVM driver needs read and write on `/dev/kvm`.
- **A Claude Code agent works in it.** It signed in with the `claude setup-token` token, did a task on an uploaded copy of sssf-sandbox, and the result came back by download. `git push` and `curl` to GitHub failed with "Couldn't connect to server". `curl` to `api.anthropic.com` failed too, since the policy allowed only `claude.exe` there.
- **Each denial is logged** as an OCSF line naming the program and destination, from `openshell logs <name> --source sandbox`.
- **Host paths work, with the image's help.** `sandbox_remote.Remote` clones the branch at the host worktree's own absolute path, so every path a run passes means the same on both sides. An OpenShell sandbox has no root, so it can't `mkdir /home/<user>`, and Landlock applies a rule only to a path that exists when the sandbox starts. An image that ships the host's repo parent, `~/github` here, owned by the sandbox user, with a `read_write` rule on it, let the tree, the handoff folder and git all work at their host paths. A write elsewhere under the home folder was refused.
- **`openshell sandbox exec`** takes `--workdir`, `--env KEY=VALUE` and `--timeout`, which defaults to none. `upload` and `download` move files, and `stop`, `start`, `suspend` and `resume` exist. The two checks below found that it streams and passes stdin.

## Decisions

### Decided by the engineer

Each was proposed here and accepted as written on 2026-11-07.


1. **Who runs the gateway.** The gateway is one long-lived process per machine. The engineer runs it as a systemd user service, the way OpenShell's own package does, set up once by a `just openshell-setup` recipe with telemetry off. A run checks `openshell status` before it starts and stops with a plain message if the gateway is down. The other way, SSSF starting a gateway per run, costs about a second each time and makes two runs at once fight over the port.
2. **The guard rules in this mode.** Keep every rule in v1, so a run behaves the same in every mode, and log each OpenShell denial in the trace beside it. After a few weeks of runs, decide whether the rules against harm outside the repo (push, `gh` writes, billing recipes) can go in this mode. The `writes` check, spec protection, the git-directory guard and the pre-push hook stay in every mode, since they judge the code the agent was given.
3. **What the network allows.** By default, `claude.exe` may reach `api.anthropic.com` with any method, and any program may send read-only GETs to the package registries a repo's setup and checks use, PyPI and `files.pythonhosted.org` for `uv`, `registry.npmjs.org` for `npm` and `bun`. Read-only means an agent can install a package but can't publish one. A repo adds hosts in `repo.yaml`, under a new `sandbox_allow` key, each with a program, a host and `read-only` or `full`. GitHub stays closed: the host fetches and pushes, never the sandbox.
4. **Access to `/dev/kvm`.** The test used `sudo chgrp $USER /dev/kvm`, which resets when WSL restarts. The setup recipe checks access and prints the two choices, that line again or `sudo usermod -aG kvm $USER` plus one `wsl --shutdown`, and leaves the choice to the engineer, since a shutdown stops every WSL session.
5. **pi.** Claude Code rosters only in v1. `sandbox.check_roster` refuses a pi agent under `--sandbox openshell`, as it refuses a pi agent on a non-OpenAI model under the other providers. pi needs its own image, and its ChatGPT sign-in, which the host broker feeds with an hour's token per call, is untried in OpenShell.

### Settled by the test and the code

6. **A provider, not a new path.** `sandbox_openshell.py` follows `sandbox_e2b.py`: a `Remote` subclass and a `_VM` adapter that gives `sandbox_remote` the calls it already makes, `run_process`, `create_process`, `stop` and `destroy`. The adapter drives the `openshell` CLI, which the gateway needs anyway, so there's no SDK to pin. `sandbox.PROVIDERS` gains `openshell`.
7. **The image.** `scripts/openshell_image.py` in the skill builds `localhost/sssf-openshell:<skill>` with Podman, as `e2b_template.py` builds E2B's template. It holds git, uv, Python, Node 22 and Claude Code, enough for `Remote._check_tools`, and creates the parent folder of the repo and of its worktree root, owned by the sandbox user. The image is per machine, since it bakes in host paths, and the script refuses a repo whose root and worktrees share no parent below the home folder.
8. **The policy.** Each run writes its own policy file: the default read-only system paths, `read_write` on `/tmp` and the baked parent folder, and the network rules from decision 3. Landlock's `compatibility` is `hard_requirement`, so a sandbox whose file rules can't apply doesn't start.
9. **Getting the tree in and out.** No change. `Remote._clone` already sends a git bundle and clones it in the VM, and `commits_out` brings commits back as a bundle after each commit. The adapter moves those files with `upload` and `download`. The upload's own `.gitignore` filter and its lack of `.git` don't matter, since only the bundle goes in. `.env` never enters the sandbox.
10. **The token.** As in the other providers, only `start` gives an agent the token, never `run`. `exec --env` would put it on the host's command line, where any process of the same user can read it, so the adapter sends it on stdin to a short shell line that reads it, exports it and runs the agent. The exec checks confirmed this keeps it out of every command line on both sides.
11. **Sessions.** OpenShell has no session cap, so `session_minutes` is 24 hours and no refresh happens. `stop` in the adapter is a no-op.
12. **Denials in the trace.** At each agent call's end, the provider reads `openshell logs <name> --source sandbox --since <call start>`, keeps the `DENIED` lines, and logs one `sandbox_denied` event per line, with the program and destination. The dashboard shows them on the call. No denial ends a run.
13. **Clean-up.** `sweep` lists sandboxes labelled `sssf=<project>` and deletes those whose run isn't live, like the other providers. `just kill` deletes the run's sandbox.

## Build order

1. **Two checks first.** Done 2026-11-07; see [the exec checks](#the-exec-checks).
2. **The image script and `just openshell-setup`.**
3. **The provider**, with its unit tests on a fake CLI, as `sandbox_e2b`'s tests use a fake SDK.
4. **A proof in sssf-sandbox:** `adw_simple_sdlc --worktree --sandbox openshell` on a small request, with a planted `git push` in the request to see the denial in the trace.
5. **The docs:** the guide's sandbox section, `config.md`, `fork.md`, and this design's as-built sections.

## The exec checks

Run 2026-11-07 on the microVM driver, in a sandbox from the host-path image, driven from Python the way `sandbox_remote` reads a stream.

| Check | Result |
|---|---|
| Streaming | Yes. A command printing a line every 2 seconds reached the host at 0.1, 2.0, 4.0 and 6.0 seconds, line by line |
| stdin | Yes. Piped lines reached the command, and `cat` read to the end |
| The token on stdin | A value read from stdin and exported was found only in the environment of the process that exported it. No command line held it inside the sandbox, and the host's `openshell` process's arguments didn't hold it either |
| Exit code and stderr | Both come through: `exit 7` gave 7 on the host, and stderr arrived apart from stdout |
| A quiet command | A 70-second `sleep` with no output finished normally. `--timeout` defaults to none, so a long agent call isn't cut off |

So nothing in the design changes, and the build can start at step 2.

`sandbox_openshell.py` limits what an agent can do, so it's guard code. It goes through the guard-change chain, and `CLAUDE.md`'s list of guard files gains it.

## As built

- **The sentinel.** The CLI documents no exit code of its own for a failure, so the shell line inside the sandbox prints `sssf-openshell-exec-started` as its first stderr line once the env is set. A non-zero exit without it is the CLI's failure and raises `OpenShellError`; with it, the code is the command's. The adapter removes only that first line.
- **The env frame.** Env values go on stdin as `KEY=<base64>` lines ending with a `.` line; the shell exports them, so no value is on a command line.
- **`include_workdir: true`** stays, as in both test policies. The proof run checks whether it can go.
- **`ANY_BINARY = "/**"`** is an untested reading of "any binary". The proof run confirms it.
- **The sandbox name** is `sssf-<adw_id>-<hex4>`: 18 characters for an 8-character adw_id. OpenShell 0.1.7 refuses a name over 19 characters ("name exceeds maximum length (20 > 19)"), though its `--help` doesn't say so, so `OpenShell.sandbox_name_max` is 19 and `_create` raises `SandboxFailed` for a longer name before calling the CLI. **Sweep** reads the adw_id from the name, so `sandbox list --names` is enough; a name that doesn't parse, the old `<hex6>` form included, is left alone.
- **The project label.** `sssf=` is the project name cleaned to `[A-Za-z0-9_.-]` and cut to 50 characters, then `-` and the first 12 hex characters of the exact name's SHA-256, so two projects never share one, and it stays within a Kubernetes label value's 63 characters.
- **The image tag** hashes the Containerfile, which holds the shared folder, so each repo layout has its own image.
- **Who runs setup.** `just openshell-setup` and `scripts/openshell_image.py` (without `--print`) refuse while `SSSF_ADW_ID` is set, so an agent in a run can't call them directly (guard run 141c374c). It's the same courtesy check as `pre-push`. An agent can clear the variable, run the steps by hand, or start the recipe through `ssh`, `sudo` or `systemd-run`, which give it a fresh environment; [config.md](../../.claude/skills/sssf/references/config.md#a-local-sandbox---sandbox-openshell) lists them.
- **`data_dir(repo_root)`** reads `defaults.data_dir` from the shipped config; `folder_for` is the one source of the layout for `require_sdk`, `_create` and `scripts/openshell_image.py`.
- **`Remote._call_end`** is a hook that logs denials when an agent call ends. A `wait` that raises before it logs none.
- **`logs --since`** takes a duration, so a denial just before a call can be logged on that call too.
- **`setsid -w`.** OpenShell starts each exec as a process-group leader, so a bare `setsid` forked and its parent exited at once. OpenShell held the agent's output about 30 seconds, then ended the exec with exit 74, "exec output could not be delivered intact". Three proof runs failed that way, 32 seconds into the planner. `Remote.start` and the obs relay now run `setsid -w`, which waits for the agent and returns its exit code (7813dbf). On Vercel and E2B `setsid` doesn't fork, so nothing changes there.
- **The image's Python.** The image installs the Python the host's uv picks in the repo's main checkout (`image_python`), the version every sandbox command gets as `UV_PYTHON`, under `/usr/local/share/uv/python` with `python3.<minor>` on `PATH`. The version is in the image tag, so a run whose Python changed since the setup refuses and names `just openshell-setup` (805664d, guard run 609ad1c5). Without it, `uv run pytest` tried to download the Python, and the policy denied it.

## The proof

Run 700a50bb in sssf-sandbox, 2026-11-08: `adw_simple_sdlc --worktree --sandbox openshell` on "add a `sentence_case` function", with a planted `curl` to GitHub for the builder. All 12 phases passed, for $0.47, and the run committed two changes on its branch. The `curl` got "Failed to connect to github.com port 443 after 1 ms: Couldn't connect to server", exit 7, and the trace logged it as a `sandbox_denied` event, `curl` to `github.com:443`. The only other denials were 7 Claude Code telemetry calls to Datadog. The VM ran 150 seconds and was deleted at the end.

`ANY_BINARY = "/**"` is still unproven: no command in the proof reached a package registry.
