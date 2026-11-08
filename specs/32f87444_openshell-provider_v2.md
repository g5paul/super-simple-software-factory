# Plan: `--sandbox openshell`, a local microVM provider

Builds steps 2, 3 and 5 of `docs/designs/openshell-sandbox.md`'s build order: the image script and `just openshell-setup`, the provider with unit tests on a fake CLI, and the docs. Step 4, the proof run, is the engineer's. Never start a gateway, create a sandbox or run `podman build` while building or testing this.

All skill paths below are under `.claude/skills/sssf/`. The hub's installed copies (`adws/adw_modules/`, `adws/adw_*.py`, `adws/tests/`) are protected and stay untouched; `sssf-update` brings the change there later.

Revision 2. This answers `spec_review_1.md`: Out of scope now lists the limits this mode inherits from `config.md` and the plan's own holes; `require_sdk(cfg)` and the `prepare` branch are gone, replaced by one `data_dir(repo_root)` helper; the Bar's wording on rules is fixed; only the first sentinel line is stripped; the setup recipe's rewrite of its unit file is documented.

## Baseline

`main` at `f52e3a5` ("Spec the guard change that adds --sandbox openshell"). Every finding is judged against that commit. At the baseline `sandbox.PROVIDERS` is `("vercel", "e2b")`, `repo_settings.RepoSettings.sandbox` is `Literal["none", "vercel", "e2b"]`, and no `openshell` code exists.

## Threat model

- An agent runs as Claude Code inside a microVM, as the sandbox user (`ubuntu` in the test image), with no root and no capabilities. It holds `CLAUDE_CODE_OAUTH_TOKEN` in its environment and can print it (research: "The token is readable").
- The network is closed except what the run's policy opens. OpenShell applies a rule to the listed binary *and every process it starts* (network-rules docs, "Binary Matching"), so any tool the agent launches can reach `api.anthropic.com` with any method, and any process can make read-only requests to PyPI and npm. The agent could therefore use the token against `api.anthropic.com` from `curl`. It can't reach GitHub or any other host.
- Landlock limits writes to `/tmp`, `/dev/null`, the sandbox's workdir and the baked folder. Inside that folder the agent can write anything, including the clone's `.git` (hooks, config). That's as in the Vercel and E2B modes: only a bundle of commits and the handoff folder come back, and the host's own guards (`writes` check, spec protection, git-directory guard, pre-push hook) judge them.
- Host side: the `openshell` CLI and `podman` run as the engineer's user. Any process of that user can read another's argv (`/proc/<pid>/cmdline`), so a value on a command line is a leak. The adapter must never put an env value on argv, on either side.
- What this guard can't stop at its level: anything the engineer's own user can do on the host. The gateway, its TLS keys under `~/.local/state/openshell/tls`, and `~/.config/sssf-sandbox/token.env` are readable by every process of that user. An agent that escaped the microVM would be that user. The forged-event holes in `docs/open.md` stay: an agent can still write forged events into its harness's stdout stream.

## Bar

Against the baseline, the change must:

- add `openshell` as a third provider, wired everywhere `vercel` and `e2b` are listed;
- keep every env value, the token above all, off every command line, host and sandbox;
- give a command exactly the env and stdin bytes it was given, and give a started agent an empty stdin;
- tell a failure of `openshell` itself from the command's own non-zero exit, and never report a CLI failure as a success;
- refuse pi agents under `openshell`;
- open no network destination beyond decision 3's two default rules (Claude Code's binary to `api.anthropic.com:443`; read-only requests to `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`) and the rules a repo's `sandbox_allow` names; GitHub stays closed by default;
- log denials in the trace, and never end a run or raise for one, or for a failed log read;
- delete only this project's sandboxes whose run isn't live.

It must never: keep an agent's file, run agent code on the host, delete or overwrite an engineer's file, change how `vercel`, `e2b`, `none` or a `--container` run behaves, change `sandbox.prepare` or the shared `require_sdk()` signature, change any guard rule, or run `sudo` from the setup recipe.

## Out of scope

Holes this change leaves open; a reviewer files them as old:

- Every guard rule stays as it is in this mode (decision 2). Dropping the rules against harm outside the repo is a later decision.
- pi in this mode (decision 5).
- The Podman and Docker drivers.
- Running a real sandbox; the engineer's proof run (step 4) does that.
- The forged-event holes in `docs/open.md`.
- **Inherited from `config.md`, "Known limits on secrets in a VM"** (`references/config.md`, around line 465): only `OBS_AUTH_TOKEN` is held back, so a secret under another `OBS_` or `SSSF_` name crosses into the sandbox; the Claude token reaches agents by design; nothing scans the commits or handoff folder that come back for secrets, so a token written into the clone reaches the host branch; a secret committed in the repo is in the clone. The `exec` stream adds none of these and closes none.
- **Inherited from `config.md`, "In a VM, a dead stream"** (around line 391): the exec stream can drop with no error; the call is then priced from the lines that arrived, and an agent that finished while its stream was dead loses its result. The `openshell` CLI process stands in for the SDK's connection here, with the same limit.
- The token is readable inside the sandbox and usable against `api.anthropic.com` by any process the agent starts (see Threat model); OpenShell's placeholder credentials don't cover a subscription token.
- Everything readable by the engineer's own user on the host: gateway keys, `token.env`.
- `sandbox_usage.py` / `just sandbox-usage`: OpenShell is free and local, so it gets no allowance tracking.
- **The plan's own holes:**
  - **No denials when `wait()` raises.** `RemoteProcess.wait` re-raises when the agent's `wait` call fails on a call that wasn't already stopped or abandoned, before the `_done` block where the hook runs. That call logs no `sandbox_denied` events. The call fails anyway; the denials stay in `openshell logs` until the sandbox is deleted.
  - **`include_workdir: true`.** The policy keeps it, so the sandbox's own workdir is `read_write` too, beyond decision 8's and the brief's list (`/tmp`, `/dev/null`, the baked folder). Both policies the 2026-11-07 tests ran had it, and in the first run Claude Code signed in and wrote its own files with nothing else writable but `/tmp` and `/dev/null`, so the agent's home is most likely that workdir. Dropping it can't be tested without a gateway and would risk every call failing in the proof run. The workdir is inside the microVM and nothing in it comes back to the host, so it widens nothing the host sees. The proof run checks whether it can go (see "For the engineer").
  - **`ANY_BINARY = "/**"`.** OpenShell's docs give no "any binary" form. The plan writes it as this glob, untested. If the gateway reads it differently, the registry rule either matches nothing (installs fail, closed) or the policy is refused at create (the run fails before any agent starts). Neither opens more. The proof run confirms it.
  - **A swapped `data_dir`.** `data_dir(repo_root)` reads the shipped `adws/adw_sssf_config/sssf.config.yaml`. A run under `SSSF_CONFIG=other.yaml` that sets another `data_dir` gets a folder that doesn't hold its tree, and `_create` refuses it with a message naming the recipe. It fails closed.
  - A denial logged within a second or two before an agent call starts can be logged on that call too, since `logs --since` takes a duration, not a timestamp.

## Findings from the CLI's `--help` (v0.1.7)

- `sandbox exec [SANDBOX] -- <COMMAND>...`: "The CLI exits with the remote command's exit code." Flags: `-n/--name`, `--workdir`, `--timeout` (seconds, `0` = none, default 0), `--no-tty`, `--no-login-shell`, `--env KEY=VALUE` (never used here). A gateway error or a missing sandbox exits non-zero too, and no code of its own is documented. **The CLI gives no clear sign telling its own failure from the command's exit.** The adapter makes one (see "The sentinel" below). The safest reading where the sentinel can't decide: the code is a failure, never a success, so a CLI failure can make a call fail but never pass.
- `sandbox create`: `--name`, `--from <image ref>`, `--policy <yaml>`, `--label key=value` (repeatable), `--detach`, `--no-tty`, `--no-auto-providers`. `--env` values "cannot contain a newline" and are for non-secrets; not used.
- `sandbox list`: `--selector key=value`, `--names` (one name per line), `--page-size` (default 100).
- `sandbox delete [NAME]...`.
- `logs [NAME]`: `--source sandbox`, `--since <duration>` such as `30s` (a duration, not a timestamp), `-n <lines>` (default 200).
- `status`: taken to exit non-zero when it can't reach a gateway (tests drive a fake; the proof run confirms).

From OpenShell's policy docs (`how-it-works/policies/network-rules`, `.../schema`): an endpoint is `{host, port, protocol: rest, enforcement: enforce, access: read-only|read-write|full}`; `read-only` allows `GET`, `HEAD` and `OPTIONS`; npm needs `allow_encoded_slash: true` for scoped packages; a binary entry is `{path: <real path or glob>}`; an empty `binaries` list matches nothing.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` (new, guard code)

Module docstring: a run's tree in a local OpenShell microVM, the work being in `sandbox_remote`; no SDK, it drives the `openshell` CLI; no session cap; the token travels on stdin, never argv; the image comes from `scripts/openshell_image.py`; denials go in the trace. Shaped like `sandbox_e2b.py`.

Constants:

- `CLI = "openshell"`.
- `SANDBOX_USER = "ubuntu"` (the user the test's microVM ran as, from `nvcr.io/nvidia/base/ubuntu:24.04`).
- `BASE_IMAGE = "nvcr.io/nvidia/base/ubuntu:24.04"`, `NODE = "24.11.1"` (as `e2b_template.py`).
- `CLAUDE_EXE = "/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"` (the test policy's path; the image installs Claude Code with `npm install -g`, which puts it there).
- `ANY_BINARY = "/**"` (see Out of scope).
- `READ_ONLY_PATHS = ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"]` (the test's list).
- `REGISTRIES`: `pypi.org`, `files.pythonhosted.org`, `registry.npmjs.org` (the last with `allow_encoded_slash: true`).
- `CONFIG = "adws/adw_sssf_config/sssf.config.yaml"`, `DATA_DIR = "adws/adw_data"` (the `SSSFConfig.defaults.data_dir` default).
- `READY_SECONDS = 180` (the first boot took about 35 s).
- `SENTINEL = "sssf-openshell-exec-started"`.
- `SETUP = "just openshell-setup"` for messages (they add "or `just sssf openshell-setup` where SSSF's recipes are a module").

Functions shared with the image script. These are the single source of the layout; `require_sdk`, `_create` and the image script all go through `folder_for` and nothing else:

- `repo_root(cwd: Path) -> Path`: the parent of `git rev-parse --path-format=absolute --git-common-dir` run in `cwd`, so a worktree gives the main checkout.
- `data_dir(root: Path) -> str`: `defaults.data_dir` from `root / CONFIG` (read with `yaml.safe_load`), or `DATA_DIR` when the file, the key or its value is missing.
- `shared_folder(root: Path, worktree_root: Path, home: Path) -> Path`: the deepest folder holding both (`os.path.commonpath` on resolved paths). Raises `ValueError` with a plain message when it isn't strictly below `home`, for example when the repo is outside the home folder, or the two share only the home folder.
- `folder_for(cwd: Path) -> Path`: `root = repo_root(cwd)`; `shared_folder(root, root / data_dir(root) / "worktrees", Path.home())`. The worktree root is built as `worktree.path_for` builds it, from the same `data_dir`.
- `containerfile(folder: Path) -> str`: the image recipe text: `FROM BASE_IMAGE`; `apt-get install` of `git curl ca-certificates python3 xz-utils`; Node `NODE` from nodejs.org into `/usr/local` (as `e2b_template.py`); uv into `/usr/local/bin` (`UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1`); `npm install -g @anthropic-ai/claude-code`; `mkdir -p <folder> && chown -R SANDBOX_USER:SANDBOX_USER <folder>`; a final `RUN node --version && uv --version && claude --version && git --version`. Shell-quote the folder (`shlex.quote`).
- `image_tag(folder: Path) -> str`: `localhost/sssf-openshell:` plus the first 12 hex of the sha256 of `containerfile(folder)`. The hash covers the skill's image definition with the folder in it, so each repo layout and each skill change gets its own tag.
- `policy(folder: Path, allow: list) -> dict` and `policy_yaml(folder, allow) -> str`:
  - `version: 1`;
  - `filesystem_policy: {include_workdir: true, read_only: READ_ONLY_PATHS, read_write: ["/tmp", "/dev/null", str(folder)]}`. A comment says why `include_workdir` stays (both test policies had it; see Out of scope);
  - `landlock: {compatibility: hard_requirement}`;
  - `network_policies`:
    - `anthropic_api`: `api.anthropic.com:443`, `protocol: rest`, `enforcement: enforce`, `access: full`, binaries `[CLAUDE_EXE]`;
    - `package_registries`: the three registry hosts at 443, `rest`, `enforce`, `access: read-only`, binaries `[ANY_BINARY]`;
    - one rule per `sandbox_allow` entry, keyed `repo_allow_<n>`: its host and port, `rest`, `enforce`, its `access` (`read-only` or `full`), binaries `[ANY_BINARY if binary == "*" else binary]`;
    - nothing else. No GitHub host appears anywhere unless a repo's `sandbox_allow` names one.

Errors: `class OpenShellError(RuntimeError)`, a failure of the CLI itself.

`require_sdk() -> None`, with the same no-argument signature as the other providers'. It raises `SystemExit` with a plain message naming `SETUP` when:

- (a) `shutil.which(CLI)` is None: "the openshell CLI isn't on PATH";
- (b) `openshell status` exits non-zero or times out: "no OpenShell gateway answers", with the CLI's last stderr line;
- (c) `folder_for(Path.cwd())` raises `ValueError`: that message;
- (d) `podman image exists <image_tag(folder)>` exits non-zero, or `podman` is missing: "the sandbox image ... isn't built".

Every subprocess here has a 30 s timeout. `sandbox.prepare` stays as it is.

`label(value) -> str`: `re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:63]`, used for both labels.

`sweep(project, live) -> list[str]`:

- Runs `openshell sandbox list --selector sssf=<label(project)> --names --page-size 1000`. A non-zero exit raises `OpenShellError`, which `sandbox.open_remote` turns into `SandboxFailed`.
- Each name `_create` makes is `sssf-<adw_id>-<6 hex>`. The adw_id is `name[len("sssf-"):].rsplit("-", 1)[0]`. A name that doesn't parse is left alone: never delete what can't be read.
- Deletes each whose adw_id isn't in `live` with `openshell sandbox delete <name>`, and returns `"<name> (<adw_id>)"` for each deletion.
- The `adw_id` label is still set on create. The name carries the id so sweep needs no JSON, whose shape v0.1.7 doesn't document. The docstring records this choice.

The env frame, `_frame(env: dict) -> bytes`: each key must match `^[A-Za-z_][A-Za-z0-9_]*$` and each value hold no NUL, else `ValueError`. One line per item, `KEY=<base64 of the UTF-8 value>\n`, then a closing line `.\n`. Base64 lets a value hold newlines, quotes, `$` and backslashes.

The sh line, `_SHELL`, run as `sh -c _SHELL sh <stdin-mode> <command> <args...>`:

```sh
while IFS= read -r l && [ "$l" != . ]; do
  k=${l%%=*}; v=$(printf %s "${l#*=}" | base64 -d; echo x); export "$k=${v%x}"
done
printf '%s\n' SENTINEL >&2
m=$1; shift
if [ "$m" = null ]; then exec "$@" </dev/null; else exec "$@"; fi
```

`read` in `sh` (dash) reads a pipe a byte at a time, so the bytes after the `.` line are exactly the `input`. The `echo x` / `${v%x}` pair keeps trailing newlines. `SENTINEL` goes in with `.replace`; no other value enters the script text. Keys are checked on the host before they reach `export`.

The sentinel: the shell writes `SENTINEL` as its first stderr line, after the env is exported and before the command runs. The adapter removes only that first line, and only when it is exactly `SENTINEL`; a later identical line from the command stays. A non-zero exit with no sentinel seen is a failure of `openshell` itself (no gateway, no sandbox, a connection dropped before start) and raises `OpenShellError` with the CLI's stderr (last 500 chars). With the sentinel seen, the code is the command's. A zero exit without the sentinel can't come from a real run; it raises `OpenShellError` too.

`_exec_argv(name, cwd, timeout) -> list[str]`: `[CLI, "sandbox", "exec", "-n", name, "--no-tty", "--no-login-shell", *(["--workdir", cwd] if cwd else []), *(["--timeout", str(ceil(timeout))] if timeout else []), "--", "sh", "-c", _SHELL, "sh"]`. It never uses `--env`.

`class _VM`:

- `__init__(self, name)`; `self.name = name`.
- `run_process(command, args=(), *, cwd=None, env=None, sudo=False, capture_output=True, input=None, kill_after=None)`:
  - `sudo` is ignored. The sandbox has no root, and the image makes the folders the run needs, owned by the sandbox user, which is why the `mkdir` and `chown` in `_clone` and `install` work.
  - Popen `_exec_argv(...) + ["input" if input is not None else "null", command, *args]` with binary stdin, stdout and stderr pipes.
  - `communicate(_frame(env or {}) + (input or b""), timeout=kill_after + 30 if kill_after else None)`. `input` may be `bytes` or `str`; encode a `str`.
  - On a host `TimeoutExpired`, kill the CLI, reap it and return code 124, as E2B does; `sandbox_remote.run` turns it into `TimeoutExpired`.
  - Decode stdout and stderr as UTF-8 with `errors="replace"`, remove the first sentinel line, apply the sentinel rule, and return `SimpleNamespace(returncode, stdout, stderr)`.
- `create_process(command, args=(), *, cwd=None, env=None) -> _Stream`: Popen the same argv with mode `null`, a line-buffered text stdout and a stdin pipe. Write `_frame(env)` and close stdin. The agent's stdin is then `/dev/null`, as `sandbox.LocalProcess`'s is.
- `stop()`: does nothing, since there's no session cap.
- `destroy()`: `openshell sandbox delete <name>`. A non-zero exit raises `OpenShellError`.

`class _Stream`, what `RemoteProcess` reads:

- `stdout` yields the Popen's lines.
- A daemon thread drains stderr into a buffer and notes whether the first line was the sentinel.
- `stderr` is the stream object itself. Its `read()` joins that thread and returns stderr without the first sentinel line.
- `wait()` waits on the Popen, joins the thread, and either raises `OpenShellError` by the sentinel rule or returns the code.

`class OpenShell(Remote)`:

- `name = "openshell"`, `keys = ()`, `session_minutes = 24 * 60`, `owner = SANDBOX_USER`, `errors = (OpenShellError,)`.
- `_create()`:
  - Computes `folder = folder_for(self.tree)`; a `ValueError` becomes `OpenShellError`.
  - Checks that `self.tree`, `self.handoff` and `self.run_.session_dir` all lie inside `folder`. If one doesn't, it raises `OpenShellError` naming `SETUP`; this covers a swapped `data_dir`.
  - Writes `policy_yaml(folder, repo_settings.load(repo_root(self.tree)).sandbox_allow)` to `self.run_.session_dir / "openshell-policy.yaml"`.
  - Names the sandbox `sssf-<adw_id>-<uuid4().hex[:6]>`.
  - Runs `openshell sandbox create --name <name> --from <image_tag(folder)> --policy <file> --label sssf=<label(project)> --label adw_id=<label(adw_id)> --detach --no-tty --no-auto-providers` with a `READY_SECONDS` timeout. It passes no `--upload`, since `Remote._clone` sends a bundle over stdin, and no `--env`. A non-zero exit raises `OpenShellError`.
  - Polls `_VM.run_process("true")` every 2 s until it succeeds. If `READY_SECONDS` pass first, it raises `OpenShellError`.
  - Returns the `_VM`.
- `_delete()`: `self.sandbox.destroy()`.
- `_call_end(started: float)`: the denial read, below.

Denials. `_call_end(started)` runs `openshell logs <vm> --source sandbox --since <ceil(time.time() - started) + 2>s -n 10000` with a 30 s timeout.

- On a non-zero exit, a timeout or any exception, it logs `self._log("sandbox_error", {"vm": self.vm, "error": "denial log read failed: ..."[-500:]})` and returns.
- Otherwise, for each line containing ` DENIED `, it matches `DENIED\s+(\S+?)(?:\(\d+\))?\s+->\s+(\S+)` and logs `self._log("sandbox_denied", {"vm": self.vm, "binary": <group 1 or "">, "destination": <group 2 or "">, "line": line.strip()[-500:]})`.
- It never raises.
- The research's example line, `NET:OPEN [MED] DENIED /usr/bin/curl(0) -> github.com:443 [reason:transparent_tcp_policy_denied]`, gives `/usr/bin/curl` and `github.com:443`.

`Box = OpenShell`.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

The smallest hook, a no-op for every other provider:

- `Remote._call_end(self, started: float) -> None`, under "what a provider writes". Docstring: "What a provider does when an agent call ends: `started` is the call's start, from time.time(). Must not raise." The body is `return`.
- `RemoteProcess.__init__`: `self.started = time.time()`.
- `RemoteProcess.wait`: inside `if not self._done:`, right after `self._done = True` and before `handoff_out`, call `self._box._call_end(self.started)` inside `try: ... except Exception: pass`. Denials are then logged on every call that reaches that block, including one whose handoff fails. A `wait` that re-raises before the block logs none (Out of scope).
- Module docstring: the provider list becomes `sandbox_vercel, sandbox_e2b, sandbox_openshell`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

- `PROVIDERS = ("vercel", "e2b", "openshell")`.
- `check_roster`: after the unknown-provider check, when `provider == "openshell"` and the roster has pi agents, raise `SystemExit(f"--sandbox openshell runs Claude Code agents only for now; pi isn't supported in this mode yet, and {names} are pi agents. Use --sandbox vercel or e2b for them, or --sandbox none.")`. The existing openai/ check then runs as before for the other providers.
- `prepare` doesn't change.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/repo_settings.py`

- `sandbox: Literal["none", "vercel", "e2b", "openshell"] = "none"`, with its comment updated.
- A new model `SandboxAllow(BaseModel)` with `extra="forbid"`:
  - `binary: str`: a path in the sandbox starting with `/`, or `"*"`. A validator refuses anything else.
  - `host: str`: non-empty, with no scheme and no `/`.
  - `port: int = Field(443, ge=1, le=65535)`.
  - `access: Literal["read-only", "full"]`.
- `sandbox_allow: list[SandboxAllow] = Field(default_factory=list)`, with a comment: the hosts `--sandbox openshell` opens beyond the defaults, each becoming one network rule.
- `templates/repo.yaml` is **not** changed.

### 5. `--sandbox` choice lists

Add `"openshell"` to the `choices` in each of:

- `.claude/skills/sssf/templates/adws/adw_build_test.py`
- `.claude/skills/sssf/templates/adws/adw_plan_build_test.py`
- `.claude/skills/sssf/templates/adws/adw_plan_build_test_quality.py`
- `.claude/skills/sssf/templates/adws/adw_simple_sdlc.py`
- `.claude/skills/sssf/templates/adws/adw_tickets.py`
- `.claude/skills/sssf/templates/adws/adw_trial.py`

Update any help text there that lists `vercel|e2b`. Grep `templates/` for `"vercel", "e2b"` and `vercel|e2b` to catch the rest, such as the docstring of `adw_modules/trial.py`.

### 6. `.claude/skills/sssf/scripts/openshell_image.py` (new)

A PEP 723 header like `e2b_template.py`'s (`dependencies = ["pyyaml"]`). Docstring: it builds `localhost/sssf-openshell:<hash>` with Podman for `--sandbox openshell`; the image is per machine and per repo layout, since it bakes in the shared folder; `just openshell-setup` runs it; usage `uv run <skill>/scripts/openshell_image.py [--repo .] [--print]`.

- It imports `sandbox_openshell` from the skill's own `templates/adws` (`sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))`). It calls `folder_for(Path(args.repo))`, `containerfile` and `image_tag`, and reads no config of its own.
- On `ValueError` it prints the plain message to stderr and exits 2. The message says the repo's root and worktree root share no folder below the home folder, so the image can't give the sandbox user one folder for both, and to move the repo, or the data folder, under the home folder.
- `--print` prints the tag and the Containerfile and exits 0 without running Podman. The tests use it.
- Otherwise it runs `podman build -t <tag> -f - <empty temp dir>` with the Containerfile on stdin, streams the output and exits with Podman's code. On success it prints the tag.

### 7. `.claude/skills/sssf/templates/justfile`: `openshell-setup`

A recipe whose comment summary line reads "set up the local OpenShell gateway and image for --sandbox openshell", written as a bash script recipe (`#!/usr/bin/env bash`, `set -euo pipefail`). It never runs `sudo`.

1. Check `/dev/kvm`. If `[ -r /dev/kvm ] && [ -w /dev/kvm ]` fails, print both fixes and `exit 1`: `sudo chgrp $USER /dev/kvm`, which lasts until WSL restarts; or `sudo usermod -aG kvm $USER`, then `wsl --shutdown` from Windows, which stops every WSL session. The engineer chooses.
2. Run `command -v openshell openshell-gateway podman`; if any is missing, print a plain message pointing at `docs/research/openshell.md`'s install notes.
3. TLS: if `~/.local/state/openshell/tls/ca.crt` is missing, run `openshell-gateway generate-certs --output-dir ~/.local/state/openshell/tls`.
4. Config: if `~/.config/openshell/gateway.toml` is missing, write `[openshell] version = 2` and `[openshell.gateway]` with `bind_address = "127.0.0.1:17670"` and `compute_driver = "vm"`.
5. Unit: write `~/.config/systemd/user/openshell-gateway.service` with:
   - `ExecStart=<path of openshell-gateway> --config %h/.config/openshell/gateway.toml`;
   - `Environment=OPENSHELL_TELEMETRY_ENABLED=false`;
   - `Environment=OPENSHELL_LOCAL_TLS_DIR=%h/.local/state/openshell/tls`;
   - `Environment=OPENSHELL_BIND_ADDRESS=127.0.0.1`;
   - `Restart=on-failure` and `WantedBy=default.target`.

   Then run `systemctl --user daemon-reload` and `systemctl --user enable --now openshell-gateway`.
6. Image: `uv run .claude/skills/sssf/scripts/openshell_image.py --repo .`.
7. Print `openshell status` and the next step.

Steps 3 and 4 only create missing files, so certs and a gateway config the engineer already has are kept. Step 5 rewrites its unit file on every run. It is SSSF's own file under SSSF's own name, and rewriting it is how a rerun picks up a moved `openshell-gateway`. The recipe's comment and `config.md` say so, so an engineer who wants their own unit uses another name. The recipe writes no other file.

### 8. Tests (new files under `.claude/skills/sssf/tests/`)

`test_sandbox_openshell.py`. A fixture writes a fake `openshell` into `tmp_path/bin`, a Python script with `#!<sys.executable>` and mode 755, and prepends that folder to `PATH` with `monkeypatch.setenv`. The fake appends `{"argv": sys.argv[1:], "stdin": <base64 of stdin bytes>}` to a JSONL file named by `FAKE_OPENSHELL_LOG`, and acts by subcommand:

- `sandbox exec ... -- <cmd...>`:
  - with `FAKE_OPENSHELL_MODE=local`, it runs `<cmd...>` locally with the recorded stdin bytes, in `--workdir`, and passes its stdout, stderr and exit code through;
  - with `FAKE_OPENSHELL_MODE=down`, it prints an error to stderr and exits 1 without running anything.
- `sandbox create`, `sandbox delete` and `status` exit with `FAKE_OPENSHELL_CODE` (default 0).
- `sandbox list` prints `FAKE_OPENSHELL_NAMES`.
- `logs` prints the file named by `FAKE_OPENSHELL_LOGS`, and exits 1 when it's unset.

Tests (each name says what it proves):

- `test_no_env_value_reaches_a_command_line`: `run_process` with an env holding a token-like value and others, then `create_process` with `CLAUDE_CODE_OAUTH_TOKEN`. Every value is absent from every recorded argv and present in the decoded stdin.
- `test_env_and_input_reach_the_command_intact`: in local mode, a small `python3 -c` command dumps the chosen `os.environ` keys and its stdin as JSON. The values hold spaces, `'`, `"`, `$HOME`, backslashes, and newlines inside and at the end. The `input` bytes hold newlines and a `.` line. Everything comes back exactly.
- `test_a_nul_in_a_value_is_refused`: `ValueError`, and nothing runs.
- `test_a_started_agent_reads_an_empty_stdin`: `create_process` of a command that reads stdin and prints its length prints 0, and the env still arrives.
- `test_exit_codes_and_stderr_pass_through`: `sh -c 'echo out; echo err >&2; exit 7'` gives 7, `out` and `err`, with no sentinel in stderr. The same holds through `create_process`'s `wait()` and `stderr.read()`. A command that prints the sentinel text itself keeps that line.
- `test_a_cli_failure_raises_the_adapters_error`: down mode raises `OpenShellError` from `run_process` and from `_Stream.wait()`. `OpenShellError` is in `OpenShell.errors`, and through `Remote._call` it becomes `OSError`.
- `test_kill_after_gives_124`: with a fake exec that sleeps, `kill_after=0.5` returns 124.
- `test_the_default_policy`: `policy(folder, [])` equals the expected dict, with the read-only list, `read_write` of `/tmp`, `/dev/null` and the folder, `include_workdir`, `hard_requirement`, and exactly the two network rules decision 3 names. No host contains `github`.
- `test_sandbox_allow_entries_become_rules`: two `SandboxAllow` entries (one with `*`, one with a path, one on the default port) give two more rules with the right binary, host, port and access. There's still no GitHub host, and the default rules are unchanged. `RepoSettings.model_validate` also refuses an entry with `access: write`, one with a relative binary path, and one with an unknown key.
- `test_data_dir_reads_the_shipped_config`: in a tmp repo with no config, `data_dir` gives `adws/adw_data`. With a copy of the shipped `.claude/skills/sssf/templates/sssf.config.yaml` it gives that file's value (or the default when the file doesn't set one), and with `defaults.data_dir` set to another value it gives that value.
- `test_create_writes_the_policy_and_passes_no_upload`: a run namespace shaped like the `remote` fixture in `test_sandbox_remote.py` (`adw_id`, `context_handoff_dir`, `session_dir`, `tracer`), inside a `tmp_path` laid out as home and repo, with `HOME` monkeypatched and the repo a `git init` checkout. With the ready probe in local mode, `_create()` records `sandbox create` argv holding `--from <image_tag>`, `--policy <session_dir>/openshell-policy.yaml`, both labels, `--detach` and `--no-tty`, and no `--upload`, `--env` or `--env-from`.
- `test_create_refuses_a_tree_outside_the_folder`: a session folder outside the shared folder raises `OpenShellError` naming `openshell-setup`, and no `sandbox create` runs.
- `test_check_roster_refuses_pi_under_openshell` and `test_check_roster_passes_claude_code_under_openshell`: build the config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, as the existing roster tests load it, and set one agent's `coding_agent` to `pi` for the refusal. The message names pi and Claude Code only.
- `test_sweep_deletes_only_dead_runs_of_this_project`: with names `sssf-live1-aaaaaa`, `sssf-dead1-bbbbbb` and `other-thing`, and `live={"live1"}`, only `sssf-dead1-bbbbbb` is deleted. The list call carried `--selector sssf=<project>`.
- `test_denial_lines_become_events`: a logs file with two `DENIED` lines (the research's example and one more) and one allowed line. `_call_end` logs two `sandbox_denied` events with the binary and destination, and the `--since` argument ends in `s`.
- `test_a_failed_log_read_is_a_sandbox_error`: with logs unset (exit 1), it logs one `sandbox_error` and raises nothing.
- `test_require_sdk_names_the_recipe`: with no CLI on PATH, then with `status` failing, each `SystemExit` message contains `openshell-setup`. `require_sdk` takes no arguments.
- `test_other_providers_call_end_is_a_no_op`: `Remote._call_end` returns None. With the E2B fake's pattern from `test_sandbox_remote.py`, a started call's `wait()` logs no `sandbox_denied` or `sandbox_error`. Copy the pattern; don't change the old tests.

`test_openshell_image.py`:

- `test_refuses_a_layout_with_no_shared_folder`: in a `git init` repo inside a tmp home (`HOME` monkeypatched), whose `adws/adw_sssf_config/sssf.config.yaml` sets `defaults.data_dir` to an absolute path outside that home, `subprocess.run([sys.executable, <script>, "--repo", repo, "--print"], env=...)` exits 2 with the plain message on stderr.
- `test_prints_the_image_for_a_normal_layout`: the same with the default data_dir exits 0, prints `localhost/sssf-openshell:` plus 12 hex, and the Containerfile `chown`s the repo root to `ubuntu`.
- `test_the_script_and_provider_agree_on_the_tag`: the printed tag equals `sandbox_openshell.image_tag(sandbox_openshell.folder_for(repo))`.

Don't edit any existing test. Run the repo's test command (Verification); every existing test must still pass.

### 9. Docs

- `.claude/skills/sssf/references/config.md`:
  - In the `--sandbox` section (around line 295), add `openshell`. It's local and free, for Claude Code only, and needs `just openshell-setup` once (the gateway as a systemd user service with telemetry off, `/dev/kvm` access, the image; the recipe rewrites its own unit file on each run). No API key is needed. Describe the network policy (Claude Code to `api.anthropic.com`, read-only package registries, GitHub closed), and denials as `sandbox_denied` events.
  - Under the existing "Known limits on secrets in a VM" (around line 465), say they hold for `openshell` too.
  - Update every `vercel|e2b` mention that lists providers (lines 49 and 377, and others grep finds).
  - In "Repo settings in `repo.yaml`", add a `sandbox_allow` row and a short example:
    ```yaml
    sandbox_allow:
      - binary: "*"
        host: api.example.com
        access: read-only
    ```
    Say each entry becomes one network rule, `port` defaults to 443, `binary` is a real path in the sandbox or `*`, and the key only matters under `--sandbox openshell`.
- `docs/guide/run.md`: the sandbox section gains `openshell`: what it's for, the setup recipe, Claude Code only.
- `docs/guide/troubleshooting.md`: the gateway-down and image-missing messages and their fix (`just openshell-setup`), and the `/dev/kvm` fix.
- `docs/guide/install.md`: only if it lists the providers.
- `docs/fork.md`: an entry for the change, in that file's existing style.
- `docs/designs/openshell-sandbox.md`: the status line says steps 2, 3 and 5 are built and the proof run is next. Add an "## As built" section covering:
  - the sentinel, and why: the CLI documents no failure code of its own;
  - the env frame on stdin: base64 lines ending with a `.` line;
  - `include_workdir`, kept from the tests;
  - `ANY_BINARY = "/**"` as an untested reading;
  - the sandbox name carrying the adw_id for `sweep`;
  - the tag hash covering the shared folder;
  - `data_dir(repo_root)` reading the shipped config;
  - the `_call_end` hook and its gap on a `wait` that raises;
  - `--since` as a duration.
- `docs/open.md`: the OpenShell row (line 22) says the build is done and the proof run (step 4) is next.
- Docstrings: `sandbox.py`'s module docstring if it names providers, `sandbox_remote.py`'s, and `trial.py`'s.
- No `app_docs/` edits. As far as the planner found, no write-up there describes sandboxes in a way this change makes wrong; the documenter checks.

## Verification

Run from the repo root:

1. The repo's test command, from `docs/sssf.md`:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
   All tests pass, including the new ones in `tests/test_sandbox_openshell.py` and `tests/test_openshell_image.py`, and no existing test file is edited.
2. `uv run .claude/skills/sssf/scripts/openshell_image.py --repo . --print` exits 0 and prints a tag. It runs no Podman.
3. `just --justfile .claude/skills/sssf/templates/justfile --list` lists `openshell-setup`. Don't run the recipe.
4. `grep -rn '"vercel", "e2b", "none"' .claude/skills/sssf/templates` finds nothing.
5. `git diff f52e3a5 -- .claude/skills/sssf/templates/adws/adw_modules/sandbox.py` shows no change to `prepare`.

## For the engineer

These are in protected paths or need a real sandbox, so the builder leaves them:

- `CLAUDE.md`: add `sandbox_openshell.py` to the guard-files list in "Guard changes".
- The proof run (build step 4): after `just openshell-setup`, run `adw_simple_sdlc --worktree --sandbox openshell` in sssf-sandbox. It should also confirm four readings this plan made without a gateway:
  - that `ANY_BINARY = "/**"` matches every binary. If `sandbox create` refuses the policy, or a registry GET from `uv` is denied, list the interpreters' real paths instead: `/usr/local/bin/uv`, `/usr/bin/python3.12`, `/usr/local/bin/node`.
  - whether Claude Code still works with `include_workdir: false`; if it does, drop it.
  - that `openshell status` exits non-zero with no gateway.
  - that `sandbox create --detach` with no command leaves a sandbox `exec` can reach.
- The hub's installed copy (`adws/adw_modules/`, `adws/adw_*.py`) gets the change with the next `sssf-update`.
