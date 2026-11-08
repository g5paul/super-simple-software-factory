# Add `--sandbox openshell`, a local microVM provider beside Vercel and E2B

`docs/designs/openshell-sandbox.md` is the design, and the engineer accepted all
its decisions on 2026-11-07. Read it whole first, then
`docs/research/openshell.md` for what the tests on this machine found. Build
steps 2, 3 and 5 of its build order: the image script and setup recipe, the
provider with its unit tests, and the docs. Step 4, the proof run, is the
engineer's, after this run.

OpenShell v0.1.7's CLI is at `~/.local/opt/openshell/bin/openshell`. Read its
`--help` output for any subcommand you use (`sandbox create`, `exec`, `list`,
`delete`, `logs`, `status`). Never start a gateway or create a sandbox: the unit
tests drive a fake CLI.

## What should change

- **The provider.** A new `adw_modules/sandbox_openshell.py`, shaped like
  `sandbox_e2b.py`: a `Remote` subclass named `OpenShell`, `Box = OpenShell`, a
  `_VM` adapter giving `sandbox_remote` the calls it makes (`run_process` with
  its `cwd`, `env`, `sudo`, `capture_output`, `input` and `kill_after`;
  `create_process`; `stop`; `destroy`), `require_sdk` and `sweep`. The adapter
  runs the `openshell` CLI with `subprocess`; there's no SDK, so `require_sdk`
  checks that the CLI is on `PATH` and that `openshell status` reaches a
  gateway, and fails with a plain message naming `just openshell-setup` if not.
  `keys` is empty: no API key is needed. `session_minutes` is 24 hours, and
  `stop` does nothing.
- **No secret on a command line.** `exec --env KEY=VALUE` would put values on
  the host's command line, readable by every process of the same user. The
  adapter never passes any env value as an argument, the agent's token above
  all (`Remote.start` puts `CLAUDE_CODE_OAUTH_TOKEN` in `create_process`'s
  env). It sends the env on stdin instead, to a short `sh` line that reads it,
  exports it, and `exec`s the command. `run_process`'s `input` follows the env
  on the same stdin, so the command reads exactly the bytes it was given. A
  started agent's stdin is `/dev/null` after the env is read, as a local run's
  is (`sandbox.LocalProcess`). Values may hold any byte but NUL, newlines
  included. The checks in the design's "The exec checks" section found that
  `exec` streams line by line, passes stdin, returns the command's exit code
  and keeps stderr apart, with no default timeout.
- **`sudo`.** A sandbox has no root. `run_process` runs a `sudo=True` command
  as the sandbox user. That works only because the image creates the parent
  folders the run uses, owned by that user. `owner` is that user's name, as
  the image sets it.
- **Telling a CLI failure from the command's.** Find in `--help`, or in how
  `exec` reports, how a failure of `openshell` itself (no gateway, no such
  sandbox) differs from the command exiting non-zero, and raise an error of
  the adapter's own type for the first, listed in `errors`, so `_call` turns
  it into `OSError` as it does for the other providers. If the CLI gives no
  clear sign, say so in the spec and pick the safest reading.
- **Creating the sandbox.** `_create` runs `openshell sandbox create` with
  `--from` the image the setup made, `--policy` a file the run writes in its
  session folder, `--label sssf=<project>` and `--label adw_id=<adw_id>`,
  `--detach`, `--no-tty`, and nothing uploaded: `Remote._clone` brings the
  branch in as a bundle over stdin.
- **The policy** the run writes: `filesystem_policy` with the read-only system
  paths from `docs/research/openshell.md`'s test plus `read_write` on `/tmp`,
  `/dev/null` and the image's parent folder; `landlock.compatibility:
  hard_requirement`; and `network_policies` from design decision 3. Claude
  Code's native binary may reach `api.anthropic.com:443` with any method. Any
  binary may send read-only requests to `pypi.org`, `files.pythonhosted.org`
  and `registry.npmjs.org`. GitHub, and every other host, stays closed. Read
  OpenShell's policy docs (`docs/research/openshell.md` lists them) for the
  read-only form.
- **`sandbox_allow` in `repo.yaml`.** A new optional key in
  `repo_settings.py`: a list of entries, each with `binary` (a path in the
  sandbox, or `*`), `host`, `port` (443 by default) and `access`
  (`read-only` or `full`). Each becomes a network rule. Leave
  `templates/repo.yaml` unchanged, since editing it flags the file for review
  in every repo; document the key in `references/config.md`.
- **Denials in the trace.** When an agent call ends, read
  `openshell logs <name> --source sandbox --since <the call's start>`, keep the
  lines that report a denied connection, and log one `sandbox_denied` event
  per line with the binary and destination. Find the place in
  `sandbox_remote` where a provider can do this at a call's end without
  changing how the other providers behave. A failed log read is logged as a
  `sandbox_error`, never raised. No denial ends a run.
- **`sweep`** lists sandboxes with the `sssf=<project>` label and deletes
  those whose `adw_id` label isn't live, as the other providers do.
- **Wiring.** `sandbox.PROVIDERS` gains `openshell`. Every `--sandbox` choice
  list in `adws/*.py` and `repo_settings.py`'s `sandbox` type gain it. Under
  `openshell`, `sandbox.check_roster` refuses any pi agent, with a message that
  says pi is Claude Code only in this mode for now (design decision 5).
- **The image.** `scripts/openshell_image.py` in the skill, beside
  `e2b_template.py`. It builds `localhost/sssf-openshell:<skill-hash>` with
  Podman from `nvcr.io/nvidia/base/ubuntu:24.04`, with git, curl, uv, Python 3,
  Node 22 or later and Claude Code, enough for `Remote._check_tools`. It
  creates the deepest folder under the home folder that holds both the repo's
  root and the worktree root `worktree.py` uses, owned by the sandbox user.
  It refuses, with a plain message, a repo whose root and worktree root share
  no folder below the home folder. The provider uses this image tag, and a
  run whose image is missing fails in `require_sdk` with a message naming the
  recipe.
- **`just openshell-setup`** in `templates/justfile`. It:
  - checks `/dev/kvm` is readable and writable, and if not prints both fixes
    from design decision 4 and stops;
  - writes a systemd user unit for the gateway with telemetry off
    (`OPENSHELL_TELEMETRY_ENABLED=false`), bound to loopback, with the VM
    driver;
  - creates the gateway's TLS certs if missing, as
    `docs/research/openshell.md` describes;
  - starts the unit and builds the image.

  It never runs `sudo`.
- **The docs.**
  - `references/config.md`: the new mode and `sandbox_allow`.
  - The guide's sandbox section, wherever `guide/` describes `--sandbox`.
  - `docs/fork.md`: the change.
  - `docs/designs/openshell-sandbox.md`: an "As built" section.
  - `docs/open.md`: the OpenShell row says the build is done and the proof
    run is next.
  - The hub's `CLAUDE.md` list of guard files gains `sandbox_openshell.py`.

## Tests

- **The token never reaches a command line.** With a fake `openshell` that
  records its argv and stdin, the token and every env value appear on stdin
  only, never in argv.
- **Env and input reach the command intact.** Through the real `sh` line with
  a fake CLI that runs it locally, `run_process` with `env` and `input` gives
  the command exactly that env and those bytes. Cover values with spaces,
  quotes, `$`, backslashes and newlines.
- **The started agent's stdin is empty** after the env is read.
- **Exit codes and stderr pass through**, and a CLI failure raises the
  adapter's error type.
- **The policy file.** The default file, and one with `sandbox_allow` entries,
  match what decision 3 says. GitHub isn't in either.
- **`check_roster`** refuses a pi agent under `openshell` and passes a Claude
  Code roster.
- **`sweep`** deletes only this project's sandboxes whose run isn't live.
- **Denial lines** become `sandbox_denied` events, and a failed log read
  becomes a `sandbox_error`.
- **The image script** refuses a layout with no shared folder.
- **Every existing test** passes unchanged.

## Out of scope

- The guard rules: every rule stays as it is in this mode (decision 2).
- pi in this mode.
- The Podman and Docker drivers.
- Running a real sandbox. The engineer runs the proof afterwards.
- The forged-event holes in `docs/open.md`, which this mode makes less
  harmful but doesn't close.
