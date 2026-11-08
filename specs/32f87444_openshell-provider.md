# Plan: `--sandbox openshell`, a local microVM provider

Builds steps 2, 3 and 5 of `docs/designs/openshell-sandbox.md`'s build order: the image script and `just openshell-setup`, the provider with unit tests on a fake CLI, and the docs. Step 4, the proof run, is the engineer's. Never start a gateway, create a sandbox or run `podman build` while building or testing this.

All skill paths below are under `.claude/skills/sssf/`. The hub's installed copies (`adws/adw_modules/`, `adws/adw_*.py`, `adws/tests/`) are protected and are not touched; `sssf-update` brings the change there later.

## Baseline

`main` at `f52e3a5` ("Spec the guard change that adds --sandbox openshell"). Every finding is judged against that commit. At the baseline `sandbox.PROVIDERS` is `("vercel", "e2b")`, `repo_settings.RepoSettings.sandbox` is `Literal["none", "vercel", "e2b"]`, and no `openshell` code exists.

## Threat model

- An agent runs as Claude Code inside a microVM, as the sandbox user (`ubuntu` in the test image), with no root and no capabilities. It holds `CLAUDE_CODE_OAUTH_TOKEN` in its environment and can print it (research: "The token is readable").
- The network is closed except what the run's policy opens. OpenShell applies a rule to the listed binary *and every process it starts* (network-rules docs, "Binary Matching"), so any tool the agent launches can reach `api.anthropic.com` with any method, and any process can make read-only requests to PyPI and npm. The agent could therefore use the token against `api.anthropic.com` from `curl`. It can't reach GitHub or any other host.
- Landlock limits writes to `/tmp`, `/dev/null`, the workdir and the baked folder. Inside that folder the agent can write anything, including the clone's `.git` (hooks, config). That's the same as the Vercel and E2B modes: only a bundle of commits comes back, and the host's own guards (`writes` check, spec protection, git-directory guard, pre-push hook) judge it.
- Host side: the `openshell` CLI and `podman` run as the engineer's user. Any process of that user can read another's argv (`/proc/<pid>/cmdline`), so a value on a command line is a leak. The adapter must never put an env value on argv, on either side.
- What this guard can't stop at its level: anything the engineer's own user can do on the host. The gateway, its TLS keys under `~/.local/state/openshell/tls`, and `~/.config/sssf-sandbox/token.env` are readable by every process of that user. An agent that escaped the microVM would be that user. The forged-event holes in `docs/open.md` stay: an agent can still write forged events into its harness's stdout stream.

## Bar

Against the baseline, the change must:

- add `openshell` as a third provider, wired everywhere `vercel` and `e2b` are listed;
- keep every env value, the token above all, off every command line, host and sandbox;
- give a command exactly the env and stdin bytes it was given, and give a started agent an empty stdin;
- tell a failure of `openshell` itself from the command's own non-zero exit, and never report a CLI failure as a success;
- refuse pi agents under `openshell`;
- open GitHub to nothing by default, and open nothing a repo's `sandbox_allow` didn't name;
- log denials in the trace, and never end a run or raise for one, or for a failed log read;
- delete only this project's sandboxes whose run isn't live.

It must never: keep an agent's file, run agent code on the host, delete or overwrite an engineer's file, change how `vercel`, `e2b`, `none` or a `--container` run behaves, change any guard rule, or run `sudo` from the setup recipe.

## Out of scope

Holes this change leaves open; a reviewer files them as old:

- Every guard rule stays as it is in this mode (design decision 2). Dropping the rules against harm outside the repo is a later decision.
- pi in this mode (decision 5).
- The Podman and Docker drivers.
- Running a real sandbox; the engineer's proof run (step 4) does that.
- The forged-event holes in `docs/open.md`.
- The token is readable inside the sandbox and usable against `api.anthropic.com` by any process the agent starts (see Threat model); OpenShell's placeholder credentials don't cover a subscription token.
- Everything readable by the engineer's own user on the host: gateway keys, `token.env`.
- `sandbox_usage.py` / `just sandbox-usage`: OpenShell is free and local, so it gets no allowance tracking.
- A denial logged within a second or two before an agent call starts can be logged on that call too, since `logs --since` takes a duration, not a timestamp.

## Findings from the CLI's `--help` (v0.1.7)

- `sandbox exec [SANDBOX] -- <COMMAND>...`: "The CLI exits with the remote command's exit code." Flags: `-n/--name`, `--workdir`, `--timeout` (seconds, `0` = none, default 0), `--no-tty`, `--no-login-shell`, `--env KEY=VALUE` (never used here). Gateway errors and a missing sandbox exit non-zero too, with no code of their own documented. **There is no clear sign telling a CLI failure from the command's exit.** The adapter makes one itself (see "The sentinel" below). Safest reading where the sentinel can't decide: the code is a failure, never a success, so a CLI failure can make a call fail but never pass.
- `sandbox create`: `--name`, `--from <image ref>`, `--policy <yaml>`, `--label key=value` (repeatable), `--detach`, `--no-tty`, `--no-auto-providers`. `--env` values "cannot contain a newline" and are for non-secrets; not used.
- `sandbox list`: `--selector key=value`, `--names` (one name per line), `--page-size` (default 100).
- `sandbox delete [NAME]...`.
- `logs [NAME]`: `--source sandbox`, `--since <duration>` such as `30s` (a duration, not a timestamp), `-n <lines>` (default 200).
- `status`: exits non-zero when it can't reach a gateway (assumed; tests drive a fake).

From OpenShell's policy docs (`how-it-works/policies/network-rules`, `.../schema`): an endpoint is `{host, port, protocol: rest, enforcement: enforce, access: read-only|read-write|full}`; `read-only` allows `GET`, `HEAD` and `OPTIONS`; npm needs `allow_encoded_slash: true` for scoped packages; a binary entry is `{path: <real path or glob>}`; an empty `binaries` list matches nothing. No "any binary" form is documented. This plan writes "any binary" as the glob `/**` in one constant, `ANY_BINARY`; the proof run confirms it (see "For the engineer").

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` (new, guard code)

Module docstring: what it is (a run's tree in a local OpenShell microVM; work is in `sandbox_remote`), no SDK (drives the `openshell` CLI), no session cap, how the token travels (stdin, never argv), the image (`scripts/openshell_image.py`), denials in the trace. Shaped like `sandbox_e2b.py`.

Constants:

- `CLI = "openshell"`.
- `SANDBOX_USER = "ubuntu"` (the user the test's microVM ran as, from `nvcr.io/nvidia/base/ubuntu:24.04`).
- `BASE_IMAGE = "nvcr.io/nvidia/base/ubuntu:24.04"`, `NODE = "24.11.1"` (as `e2b_template.py`).
- `CLAUDE_EXE = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"` (the test policy's path; the image installs Claude Code with `npm install -g`, which puts it there).
- `ANY_BINARY = "/**"`.
- `READ_ONLY_PATHS = ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"]` (the test's list).
- `REGISTRIES`: `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` (the last with `allow_encoded_slash: true`).
- `READY_SECONDS = 180` (the first boot took about 35 s).
- `SENTINEL = "sssf-openshell-exec-started"`.
- `SETUP = "just openshell-setup"` for messages (say "or `just sssf openshell-setup` where SSSF's recipes are a module").

Functions shared with the image script:

- `shared_folder(repo_root: Path, worktree_root: Path, home: Path) -> Path`: the deepest folder holding both (`os.path.commonpath` on resolved paths). Raises `ValueError` with a plain message when it isn't strictly below `home` (e.g. the repo is outside the home folder, or the two share only the home folder).
- `layout(cwd: Path, data_dir: str) -> tuple[Path, Path]`: repo root = parent of `git rev-parse --path-format=absolute --git-common-dir` run in `cwd` (the main checkout, also from a worktree); worktree root = `repo_root / data_dir / "worktrees"` (as `worktree.path_for`).
- `containerfile(folder: Path) -> str`: the image recipe text: `FROM BASE_IMAGE`; apt-get install `git curl ca-certificates python3 xz-utils`; Node `NODE` from nodejs.org into `/usr/local` (as `e2b_template.py`); uv into `/usr/local/bin` (`UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1`); `npm install -g @anthropic-ai/claude-code`; `mkdir -p <folder> && chown -R SANDBOX_USER:SANDBOX_USER <folder>`; a final `RUN node --version && uv --version && claude --version && git --version`. Shell-quote the folder (`shlex.quote`).
- `image_tag(folder: Path) -> str`: `localhost/sssf-openshell:` + first 12 hex of sha256 of `containerfile(folder)`. The hash is the skill's image definition, folder included, so each repo layout and each skill change gets its own tag.
- `policy(folder: Path, allow: list) -> dict` and `policy_yaml(...) -> str`: `version: 1`; `filesystem_policy: {include_workdir: true, read_only: READ_ONLY_PATHS, read_write: ["/tmp", "/dev/null", str(folder)]}` (`include_workdir` as both test policies had it: the agent's home and Claude Code's own files live there); `landlock: {compatibility: hard_requirement}`; `network_policies`:
  - `anthropic_api`: `api.anthropic.com:443`, `protocol: rest`, `enforcement: enforce`, `access: full`, binaries `[CLAUDE_EXE]`;
  - `package_registries`: the three registry hosts at 443, `rest`, `enforce`, `access: read-only`, binaries `[ANY_BINARY]`;
  - one rule per `sandbox_allow` entry, keyed `repo_allow_<n>`: its host and port, `rest`, `enforce`, its `access` (`read-only` or `full`), binaries `[ANY_BINARY if binary == "*" else binary]`.
  - Nothing else. No GitHub host appears anywhere unless a repo's `sandbox_allow` names it.

Errors: `class OpenShellError(RuntimeError)`: a failure of the CLI itself.

`require_sdk(cfg=None) -> None`: raises `SystemExit` with a plain message naming `SETUP` when (a) `shutil.which(CLI)` is None ("the openshell CLI isn't on PATH"), (b) `openshell status` exits non-zero ("no OpenShell gateway answers"; include the CLI's last stderr line), (c) the layout has no shared folder (the `ValueError`'s message), or (d) `podman image exists <image_tag(folder)>` exits non-zero or `podman` is missing ("the sandbox image ... isn't built"). `data_dir` is `cfg.defaults.data_dir` when cfg is given, else `"adws/adw_data"`. Every subprocess here has a timeout (30 s) and a `TimeoutExpired` gives message (b).

`sweep(project, live) -> list[str]`: `openshell sandbox list --selector sssf=<label(project)> --names --page-size 1000`. Non-zero raises `OpenShellError` (`sandbox.open_remote` turns it into `SandboxFailed`). Each name made by `_create` is `sssf-<adw_id>-<6 hex>`; parse the adw_id with `name[len("sssf-"):].rsplit("-", 1)[0]`. A name that doesn't parse is left alone (never delete what can't be read). Delete each whose adw_id isn't in `live` with `openshell sandbox delete <name>`; return `"<name> (<adw_id>)"` per deletion. The `adw_id` label is still set on create; the name carries it so sweep needs no JSON whose shape v0.1.7 doesn't document. Record that choice in the docstring.

`label(value) -> str`: `re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:63]`, used for both labels.

The env frame (`_frame(env: dict) -> bytes`): for each item, check the key matches `^[A-Za-z_][A-Za-z0-9_]*$` and the value holds no NUL, else `ValueError`; emit `KEY=<base64 of the UTF-8 value>\n`; end with a line `.\n`. Base64 lets a value hold newlines, quotes, `$` and backslashes.

The sh line (`_SHELL`), run as `sh -c _SHELL sh <stdin-mode> <command> <args...>`:

```sh
while IFS= read -r l && [ "$l" != . ]; do
  k=${l%%=*}; v=$(printf %s "${l#*=}" | base64 -d; echo x); export "$k=${v%x}"
done
printf '%s\n' SENTINEL >&2
m=$1; shift
if [ "$m" = null ]; then exec "$@" </dev/null; else exec "$@"; fi
```

`read` in `sh` (dash) reads a pipe a byte at a time, so the bytes after the `.` line are exactly the `input`. The `echo x` / `${v%x}` pair keeps trailing newlines. Put `SENTINEL` in with an f-string or `.replace`; no other value enters the script text. Keys are checked host-side before they reach `export`.

The sentinel: the shell writes `SENTINEL` as its first stderr line, after the env is exported and before the command runs. The adapter strips that line from stderr. A non-zero exit with no sentinel seen is a failure of `openshell` itself (no gateway, no sandbox, a dropped connection before start) and raises `OpenShellError` with the CLI's stderr (last 500 chars). With the sentinel seen, the code is the command's. A zero exit without the sentinel is impossible for a real run; treat it as `OpenShellError` too.

`_exec_argv(name, cwd, timeout) -> list[str]`: `[CLI, "sandbox", "exec", "-n", name, "--no-tty", "--no-login-shell", *(["--workdir", cwd] if cwd else []), *(["--timeout", str(ceil(timeout))] if timeout else []), "--", "sh", "-c", _SHELL, "sh"]`. Never `--env`.

`class _VM`:

- `__init__(self, name)`; `self.name = name`.
- `run_process(command, args=(), *, cwd=None, env=None, sudo=False, capture_output=True, input=None, kill_after=None)`: `sudo` is ignored (the sandbox has no root; the image makes the folders the run needs, owned by the sandbox user, which is why `_clone`'s and `install`'s `mkdir`/`chown` work). Popen `_exec_argv(...) + ["input" if input is not None else "null", command, *args]` with stdin, stdout, stderr pipes, binary. `communicate(_frame(env or {}) + (input or b""), timeout=kill_after + 30 if kill_after else None)`. `input` may be `bytes` or `str` (encode `str`). On host `TimeoutExpired`: kill the CLI, reap it, return code 124 (as E2B does; `sandbox_remote.run` turns it into `TimeoutExpired`). Decode stdout and stderr as UTF-8 with `errors="replace"`, strip the sentinel line, apply the sentinel rule, return `SimpleNamespace(returncode, stdout, stderr)`.
- `create_process(command, args=(), *, cwd=None, env=None) -> _Stream`: Popen the same argv with mode `null`, text stdout line-buffered, stdin a pipe: write `_frame(env)`, close stdin. The agent's stdin is `/dev/null`, as `sandbox.LocalProcess`'s is.
- `stop()`: does nothing (no session cap).
- `destroy()`: `openshell sandbox delete <name>`; non-zero raises `OpenShellError`.

`class _Stream` (what `RemoteProcess` reads): `stdout` yields the Popen's lines; a daemon thread drains stderr into a buffer, noting the sentinel; `stderr` is the object itself, whose `read()` joins that thread and returns stderr without the sentinel line; `wait()` waits on the Popen, joins the thread, and raises `OpenShellError` by the sentinel rule, else returns the code.

`class OpenShell(Remote)`:

- `name = "openshell"`, `keys = ()`, `session_minutes = 24 * 60`, `owner = SANDBOX_USER`, `errors = (OpenShellError,)`.
- `_create()`: data_dir from `getattr(self.run_, "cfg", None)` (default `"adws/adw_data"`), `folder = shared_folder(*layout(self.tree, data_dir), Path.home())` (a `ValueError` becomes `OpenShellError`). Check `self.tree`, `self.handoff` and `self.run_.session_dir` are all inside `folder`, else `OpenShellError` naming `SETUP`. Write `policy_yaml(folder, allow)` to `self.run_.session_dir / "openshell-policy.yaml"`, where `allow` is `repo_settings.load(<repo root>).sandbox_allow`. Name `sssf-<adw_id>-<uuid4().hex[:6]>`. Run `openshell sandbox create --name <name> --from <image_tag(folder)> --policy <file> --label sssf=<label(project)> --label adw_id=<label(adw_id)> --detach --no-tty --no-auto-providers` (timeout `READY_SECONDS`), no `--upload` (`Remote._clone` sends a bundle over stdin), no `--env`. Non-zero raises `OpenShellError`. Then poll `_VM.run_process("true")` until it succeeds or `READY_SECONDS` pass (raise `OpenShellError` then), sleeping 2 s between tries. Return the `_VM`.
- `_delete()`: `self.sandbox.destroy()`.
- `_call_end(started: float)`: the denial read (below).

Denials: `_call_end(started)` runs `openshell logs <vm> --source sandbox --since <ceil(time.time() - started) + 2>s -n 10000` with a 30 s timeout. Non-zero exit, a timeout or any exception: `self._log("sandbox_error", {"vm": self.vm, "error": "denial log read failed: ..."[-500:]})`, return. Otherwise for each line containing ` DENIED `: match `DENIED\s+(\S+?)(?:\(\d+\))?\s+->\s+(\S+)` and log `self._log("sandbox_denied", {"vm": self.vm, "binary": <group 1 or "">, "destination": <group 2 or "">, "line": line.strip()[-500:]})`. Never raise from `_call_end`. The research's example line: `NET:OPEN [MED] DENIED /usr/bin/curl(0) -> github.com:443 [reason:transparent_tcp_policy_denied]` gives `/usr/bin/curl` and `github.com:443`.

`Box = OpenShell`.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

The smallest hook, a no-op for every other provider:

- `Remote._call_end(self, started: float) -> None`: docstring "What a provider does when an agent call ends: `started` is the call's start, from time.time(). Must not raise." Body `return`. Put it under "what a provider writes".
- `RemoteProcess.__init__`: `self.started = time.time()`.
- `RemoteProcess.wait`: inside `if not self._done:`, right after `self._done = True` and before `handoff_out`, call `self._box._call_end(self.started)` inside `try: ... except Exception: pass`. So denials are logged on every call, also one whose handoff fails.
- Module docstring: the provider list becomes `sandbox_vercel, sandbox_e2b, sandbox_openshell`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

- `PROVIDERS = ("vercel", "e2b", "openshell")`.
- `check_roster`: after the unknown-provider check, if `provider == "openshell"` and the roster has pi agents, `raise SystemExit(f"--sandbox openshell runs Claude Code agents only for now; pi isn't supported in this mode yet, and {names} are pi agents. Use --sandbox vercel or e2b for them, or --sandbox none.")`. The existing openai/ check then runs for the other providers as before.
- `prepare`: call `module.require_sdk(cfg)` when `provider == "openshell"`, else `module.require_sdk()` as now.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

- `sandbox: Literal["none", "vercel", "e2b", "openshell"] = "none"`, comment updated.
- New model `SandboxAllow(BaseModel)`, `extra="forbid"`: `binary: str` (a path in the sandbox, starting with `/`, or `"*"`; a validator refuses anything else), `host: str` (non-empty, no scheme, no `/`), `port: int = Field(443, ge=1, le=65535)`, `access: Literal["read-only", "full"]`.
- `sandbox_allow: list[SandboxAllow] = Field(default_factory=list)` with a comment: hosts `--sandbox openshell` opens beyond the defaults, each a network rule.
- `templates/repo.yaml` is **not** changed.

### 5. `--sandbox` choice lists

Add `"openshell"` to the `choices` in each of:

- `.claude/skills/sssf/templates/adws/adw_build_test.py`
- `.claude/skills/sssf/templates/adws/adw_plan_build_test.py`
- `.claude/skills/sssf/templates/adws/adw_plan_build_test_quality.py`
- `.claude/skills/sssf/templates/adws/adw_simple_sdlc.py`
- `.claude/skills/sssf/templates/adws/adw_tickets.py`
- `.claude/skills/sssf/templates/adws/adw_trial.py`

and update any help text there that lists `vercel|e2b`. Grep `templates/` for `"vercel", "e2b"` and `vercel|e2b` to catch the rest (`adw_modules/trial.py`'s docstring).

### 6. `.claude/skills/sssf/scripts/openshell_image.py` (new)

PEP 723 header like `e2b_template.py` (`dependencies = ["pyyaml"]`). Docstring: builds `localhost/sssf-openshell:<hash>` with Podman for `--sandbox openshell`; per machine and repo layout, since it bakes in the shared folder; run by `just openshell-setup`; usage `uv run <skill>/scripts/openshell_image.py [--repo .] [--print]`.

- Imports `sandbox_openshell` from the skill's own `templates/adws` (`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))`), so the script and the provider share `containerfile`, `image_tag`, `shared_folder` and `layout`.
- Reads `defaults.data_dir` from `<repo>/adws/adw_sssf_config/sssf.config.yaml` if present, else `adws/adw_data`.
- On `ValueError` from `shared_folder`, prints the plain message to stderr and exits 2. Message: the repo's root and worktree root share no folder below the home folder, so the image can't give the sandbox user a folder for both; move the repo under the home folder.
- `--print`: prints the tag and the Containerfile and exits 0 without running Podman (used by the tests).
- Otherwise: `podman build -t <tag> -f - <empty temp dir>` with the Containerfile on stdin, streaming output; exit with Podman's code; print the tag on success.

### 7. `.claude/skills/sssf/templates/justfile`: `openshell-setup`

A recipe with a comment summary line ("set up the local OpenShell gateway and image for --sandbox openshell"), as a bash script recipe (`#!/usr/bin/env bash`, `set -euo pipefail`). It never runs `sudo`.

1. `/dev/kvm` check: if `[ -r /dev/kvm ] && [ -w /dev/kvm ]` fails, print both fixes and `exit 1`: `sudo chgrp $USER /dev/kvm` (lasts until WSL restarts), or `sudo usermod -aG kvm $USER` then `wsl --shutdown` from Windows (stops every WSL session). The engineer chooses.
2. `command -v openshell openshell-gateway podman` or a plain message pointing at `docs/research/openshell.md`'s install notes.
3. TLS: if `~/.local/state/openshell/tls/ca.crt` is missing, `openshell-gateway generate-certs --output-dir ~/.local/state/openshell/tls`.
4. Config: write `~/.config/openshell/gateway.toml` if missing: `[openshell] version = 2`, `[openshell.gateway] bind_address = "127.0.0.1:17670"`, `compute_driver = "vm"`.
5. Unit: write `~/.config/systemd/user/openshell-gateway.service`: `ExecStart=<path of openshell-gateway> --config %h/.config/openshell/gateway.toml`, `Environment=OPENSHELL_TELEMETRY_ENABLED=false`, `Environment=OPENSHELL_LOCAL_TLS_DIR=%h/.local/state/openshell/tls`, `Environment=OPENSHELL_BIND_ADDRESS=127.0.0.1`, `Restart=on-failure`, `WantedBy=default.target`. Then `systemctl --user daemon-reload` and `systemctl --user enable --now openshell-gateway`.
6. Image: `uv run .claude/skills/sssf/scripts/openshell_image.py --repo .`.
7. Print `openshell status` and the next step.

Overwrite nothing the engineer wrote: steps 3 and 4 only create missing files; step 5 writes only SSSF's own unit file name.

### 8. Tests (new files under `.claude/skills/sssf/tests/`)

`test_sandbox_openshell.py`. A fixture writes a fake `openshell` (a Python script with `#!<sys.executable>`, mode 755) into `tmp_path/bin` and prepends it to `PATH` with `monkeypatch.setenv`. The fake appends `{"argv": sys.argv[1:], "stdin": <base64 of stdin bytes>}` to a JSONL file named by `FAKE_OPENSHELL_LOG`, and acts by its subcommand:

- `sandbox exec ... -- <cmd...>`: with `FAKE_OPENSHELL_MODE=local` runs `<cmd...>` locally with the recorded stdin bytes, in `--workdir`, passing its stdout, stderr and exit code through; with `FAKE_OPENSHELL_MODE=down` prints an error to stderr and exits 1 without running anything.
- `sandbox create`, `sandbox delete`, `status`: exit with `FAKE_OPENSHELL_CODE` (default 0).
- `sandbox list`: prints `FAKE_OPENSHELL_NAMES`.
- `logs`: prints the file named by `FAKE_OPENSHELL_LOGS`; exits 1 when it's unset.

Tests (each name says what it proves):

- `test_no_env_value_reaches_a_command_line`: `run_process` with an env holding a token-like value and others, and `create_process` with `CLAUDE_CODE_OAUTH_TOKEN`; every value is absent from every recorded argv and present in the decoded stdin.
- `test_env_and_input_reach_the_command_intact`: local mode, `run_process("sh", ["-c", "env; cat"]...)` or better a small python3 command that dumps `os.environ[k]` for chosen keys and stdin as JSON; values with spaces, `'`, `"`, `$HOME`, backslashes, embedded and trailing newlines, and `input` bytes with newlines and a `.` line; all come back exactly.
- `test_a_nul_in_a_value_is_refused`: `ValueError`, nothing run.
- `test_a_started_agent_reads_an_empty_stdin`: `create_process` of a command that reads stdin and prints its length; it prints 0; the env still arrived.
- `test_exit_codes_and_stderr_pass_through`: `sh -c 'echo out; echo err >&2; exit 7'` gives 7, `out`, `err` and no sentinel in stderr; same through `create_process`'s `wait()` and `stderr.read()`.
- `test_a_cli_failure_raises_the_adapters_error`: down mode raises `OpenShellError` from `run_process` and from `_Stream.wait()`; `OpenShellError` is in `OpenShell.errors`; through `Remote._call` it becomes `OSError`.
- `test_kill_after_gives_124`: a fake exec that sleeps; `kill_after=0.5` returns 124.
- `test_the_default_policy`: `policy(folder, [])` equals the expected dict: the read-only list, `read_write` `/tmp`, `/dev/null`, folder; `include_workdir`; `hard_requirement`; exactly two network rules as decision 3 says; no host containing `github`.
- `test_sandbox_allow_entries_become_rules`: two `SandboxAllow` entries (one `*`, one path, one default port) give two more rules with the right binary, host, port and access; still no GitHub host; the default rules unchanged. Also `RepoSettings.model_validate` refuses an entry with `access: write`, a relative binary path, and an unknown key.
- `test_create_writes_the_policy_and_passes_no_upload`: a `Remote`-style run namespace (copy the `remote` fixture shape in `test_sandbox_remote.py`: `adw_id`, `context_handoff_dir`, `session_dir`, `tracer`) inside a `tmp_path` laid out as home/repo with `HOME` monkeypatched; `_create()` with the ready probe in local mode records `sandbox create` argv with `--from <image_tag>`, `--policy <session_dir>/openshell-policy.yaml`, both labels, `--detach`, `--no-tty`, and no `--upload`, `--env` or `--env-from`.
- `test_check_roster_refuses_pi_under_openshell` and `test_check_roster_passes_claude_code_under_openshell`: build the config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml` (as the existing roster tests load it), set one agent's `coding_agent` to `pi` for the refusal; assert the message names pi and Claude Code only.
- `test_sweep_deletes_only_dead_runs_of_this_project`: names `sssf-live1-aaaaaa`, `sssf-dead1-bbbbbb`, `other-thing`; `live={"live1"}`; only `sssf-dead1-bbbbbb` is deleted; the list call carried `--selector sssf=<project>`.
- `test_denial_lines_become_events`: a logs file with two `DENIED` lines (the research's example and one more) and an allowed line; `_call_end` logs two `sandbox_denied` events with the binary and destination; the `--since` argument ends in `s`.
- `test_a_failed_log_read_is_a_sandbox_error`: logs unset (exit 1) logs one `sandbox_error` and raises nothing.
- `test_require_sdk_names_the_recipe`: no CLI on PATH, then `status` failing, each `SystemExit` whose message contains `openshell-setup`.
- `test_other_providers_call_end_is_a_no_op`: `Remote._call_end` exists and returns None; with the existing E2B fake (`test_sandbox_remote.py`), a started call's `wait()` logs no `sandbox_denied` / `sandbox_error`. (Keep this light: import the fixture pattern, don't change the old tests.)

`test_openshell_image.py`:

- `test_refuses_a_layout_with_no_shared_folder`: a repo whose `sssf.config.yaml` sets `defaults.data_dir` to an absolute path outside the home folder (`HOME` monkeypatched to a tmp folder, the repo inside it, `git init` it); `subprocess.run([sys.executable, <script>, "--repo", repo, "--print"])` exits 2 with the plain message on stderr.
- `test_prints_the_image_for_a_normal_layout`: same with the default data_dir; exits 0, prints `localhost/sssf-openshell:` plus 12 hex, and the Containerfile `chown`s the repo root to `ubuntu`.
- `test_the_script_and_provider_agree_on_the_tag`: the printed tag equals `sandbox_openshell.image_tag(folder)`.

Don't edit any existing test. Run the repo's test command (Verification) and every existing test must still pass.

### 9. Docs

- `.claude/skills/sssf/references/config.md`: in the `--sandbox` section (around line 295), add `openshell`: local, free, Claude Code only, needs `just openshell-setup` once (gateway as a systemd user service, telemetry off, `/dev/kvm` access, the image); no API key; the network policy (Claude Code to `api.anthropic.com`, read-only package registries, GitHub closed); denials as `sandbox_denied` events. Update every `vercel|e2b` mention that lists providers (lines 49, 377 and others found by grep). In "Repo settings in `repo.yaml`" add a `sandbox_allow` row and a short example:
  ```yaml
  sandbox_allow:
    - binary: "*"
      host: api.example.com
      access: read-only
  ```
  saying each entry becomes one network rule, `port` defaults to 443, `binary` is a real path in the sandbox or `*`, and the key only matters under `--sandbox openshell`.
- `docs/guide/run.md`: the sandbox section gains `openshell` (what it's for, the setup recipe, Claude Code only). `docs/guide/troubleshooting.md`: the gateway-down and image-missing messages and their fix (`just openshell-setup`), and the `/dev/kvm` fix. `docs/guide/install.md`: only if it lists the providers.
- `docs/fork.md`: an entry for the change, in that file's existing style.
- `docs/designs/openshell-sandbox.md`: the status line says built (steps 2, 3, 5), proof run next; add an "## As built" section: the sentinel and why (no documented CLI-failure code), the env frame on stdin (base64 lines, `.` end), `ANY_BINARY = "/**"` as an untested reading, the sandbox name carrying the adw_id for `sweep`, the tag hash including the shared folder, `require_sdk(cfg)`, the `_call_end` hook, `--since` as a duration.
- `docs/open.md`: the OpenShell row (line 22) says the build is done and the proof run (step 4) is next.
- Docstrings: `sandbox.py`'s module docstring if it names providers, `sandbox_remote.py`'s, `trial.py`'s.
- No `app_docs/` edits. No earlier write-up there describes sandboxes in a way this change makes wrong as far as the planner found; the documenter checks.

## Verification

Run from the repo root:

1. The repo's test command (from `docs/sssf.md`):
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
   All tests pass, the new ones in `tests/test_sandbox_openshell.py` and `tests/test_openshell_image.py` included, and no existing test file changed (`git diff --stat` shows no edit to an existing test).
2. `uv run .claude/skills/sssf/scripts/openshell_image.py --repo . --print` exits 0 and prints a tag; it runs no Podman.
3. `just --justfile .claude/skills/sssf/templates/justfile --list` lists `openshell-setup` (don't run the recipe).
4. `grep -rn '"vercel", "e2b", "none"' .claude/skills/sssf/templates` finds nothing.

## For the engineer

These are in protected paths or need a real sandbox, so the builder leaves them:

- `CLAUDE.md`: add `sandbox_openshell.py` to the guard-files list in "Guard changes".
- The proof run (build step 4): `adw_simple_sdlc --worktree --sandbox openshell` in sssf-sandbox, after `just openshell-setup`. It should also confirm three readings this plan made without a gateway: that `ANY_BINARY = "/**"` matches every binary in a policy (if `sandbox create` refuses the policy, or a registry GET from `uv` is denied, list the interpreters' real paths instead: `/usr/local/bin/uv`, `/usr/bin/python3.12`, `/usr/local/bin/node`), that `openshell status` exits non-zero with no gateway, and that `sandbox create --detach` with no command leaves a sandbox `exec` can reach.
- The hub's installed copy (`adws/adw_modules/`, `adws/adw_*.py`) gets the change with the next `sssf-update`.
