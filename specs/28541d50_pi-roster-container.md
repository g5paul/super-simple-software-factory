# Plan: let a pi roster run in the operator container

Build decisions 8 to 16 of `docs/designs/container-operator.md` (section "pi in the container"). Those decisions are the source of the bar. Every path below is from the repo root. All code changes are in the skill (`.claude/skills/sssf/`). Nothing under `adws/` (the hub's own stamped copy) changes.

## Baseline

`main` at `a0d1af9` ("Settle pi in the container in container-operator.md"). Every finding is judged against that commit. At the baseline:

- `install.py --container` refuses any roster without `coding_agent: claude_code` (`.claude/skills/sssf/scripts/install.py:546-551`).
- The image has no node or pi. `run.sh` passes in only the Claude token. It `exec`s `podman run` with Podman's default network and rebuilds only when the image is missing.
- `agent_pi.run` passes the operator's environment to a LOCAL tree unchanged. Only a remote tree gets a token (`_in_vm` → `vm_token`).

## Threat model

Inside the container, agents run as the engineer's own uid (`--userns=keep-id`) with `--dangerously-skip-permissions` (Claude Code) or pi's bypass mode. An agent can read and write everything the container mounts: the repo (its `.env` included), `~/.config/sssf`, the `sssf-operator-home` volume, and the pi token socket. It can open any network connection the container has, including the forwarded `127.0.0.1:43190`. It can't change the operator's process environment, so it can't change `SSSF_PI_TOKEN_SOCKET` for later agent calls. It also can't reach the host's `~/.pi/agent`, and with it the 30-day refresh token and pi's other credentials, because that folder isn't mounted.

What this guard can't stop at its level:

- Any process in the container can connect to the socket and mint a one-hour ChatGPT access token, for as long as the session is open (decision 9, a known limit). Every process there runs as one user, so a socket secret would be one that agents could read.
- An agent can read the token from its own `OPENAI_API_KEY`, as on the host and in a VM.
- An agent can write a `~/.pi/agent/auth.json` into the home volume. The check catches an `openai` entry before the next pi agent call. It doesn't catch other providers, and it doesn't catch other pi settings files.
- An agent can edit an extension inside the repo, and the edited one loads at the next call. A host run has the same exposure.

## Bar

The change must:

1. Hand each pi agent call in the container a fresh token from the host broker as `OPENAI_API_KEY`, and only when `SSSF_PI_TOKEN_SOCKET` is set and the tree is local (`sandbox.LOCAL`).
2. Stop a pi agent call before pi starts when the broker answers with an error, can't be reached, or gives no answer. The stop carries the broker's message.
3. Stop a pi agent call before pi starts when the container's `~/.pi/agent/auth.json` has a top-level `openai` key, or can't be read as a JSON object. The stop names the file and says to remove the entry. Only key names are read. The values are never logged, printed, or put in a message.
4. Stop a container run before it starts (no trace rows, no worktree) when a pi agent's `harness_engineering` path is outside the repo, `~` paths included. The message names the agent and the path. A path inside the repo passes whether it is committed or not.
5. Keep the refresh token on the host. The broker never logs or prints a token, and refuses a credential that starts with `sk-`.
6. Leave a host run (no `SSSF_PI_TOKEN_SOCKET`), a VM run (`--sandbox vercel|e2b`, from anywhere), and a Claude Code roster in the container exactly as at the baseline. In particular, `_in_vm` and `vm_token` are unchanged, and in the container no `OBS_DISABLE` is set and `OBS_AUTH_TOKEN` is kept.

It must never:

- Mount the host's `~/.pi` or any file in it into the container.
- Keep an agent's file, run agent code on the host (the broker runs only `pi auth print-bearer-token …`, with a fixed argv, and reads nothing from the client), or delete or overwrite an engineer's file. The baseline does none of these.
- Make a Claude Code roster depend on the broker or on a host pi sign-in.

## Out of scope

These holes stay open. A reviewer should file them as old holes:

- A VM run launched from inside the container. `sandbox.prepare` and `_in_vm` still call `vm_token`, which runs the container's own `pi` and fails without a sign-in. Nothing routes `vm_token` through the socket.
- Minting tokens through the socket while the session is open (decision 9). Rate limiting and per-agent secrets are out of scope.
- An `auth.json` entry for a provider other than `openai`, and pi auth stored anywhere other than `~/.pi/agent/auth.json` (for example under a `PI_CODING_AGENT_DIR` override).
- A `harness_engineering` path inside the repo that is a symlink to a file outside it. The check uses `os.path.normpath`, as `extensions_in_clone` does, and doesn't resolve links. Imports of an extension aren't followed either.
- Podman without pasta (for example podman 4 on slirp4netns). `run.sh` would fail at `--network pasta:…`. No fallback.
- The real-container proof (decision 16). The engineer runs it by hand after the merge. See "For the engineer".

## Changes

### 1. `.claude/skills/sssf/templates/container/pi_token_broker.py` (new)

A stdlib-only Python 3 script, run on the host by `run.sh` as `python3 pi_token_broker.py <socket path>`. It must work on Ubuntu 22.04's python3 (3.10), so use no 3.11+ syntax.

- Constants: `MIN_EXPIRY = "35m"`, `PI = os.environ.get("PI_PATH", "pi")`, `PI_TIMEOUT = 120` (seconds).
- Protocol: a client connects and sends nothing. The broker writes exactly one line and closes the connection:
  - `OK <token>\n` on success.
  - `ERR <message>\n` on failure. The message is one line with no token in it.

  Document this in the module docstring. The client in `agent_pi` (step 3) relies on it.
- `mint() -> tuple[bool, str]`: runs `[PI, "auth", "print-bearer-token", "--provider", "openai", "--min-expiry", MIN_EXPIRY]` with `capture_output=True, text=True, timeout=PI_TIMEOUT, check=False`, then:
  - `FileNotFoundError`: `(False, "pi is not installed on the host, so the container can't get a ChatGPT token. Install pi on the host, sign in with `pi`, then /login.")`
  - `TimeoutExpired`: `(False, f"pi auth print-bearer-token on the host took longer than {PI_TIMEOUT}s.")`
  - A non-zero exit or empty stdout: `(False, f"pi has no ChatGPT sign-in on the host to hand the container a token from (pi auth print-bearer-token --provider openai exited {code}). Sign in with `pi` on the host, then /login.")`. This is worded like `vm_token`'s message. Do not include pi's stderr.
  - A token starting with `sk-`: `(False, "pi's openai credential on the host is an API key, not the ChatGPT sign-in, and SSSF doesn't put API keys in the container. Sign in to ChatGPT with `pi` on the host and /login.")`
  - Otherwise: `(True, token)`, where `token` is the stripped stdout.
- The handler, a `socketserver.BaseRequestHandler`: calls `mint()` and writes `OK …` or `ERR …`. It ignores `BrokenPipeError` and `ConnectionResetError`. Use `socketserver.ThreadingUnixStreamServer` with `daemon_threads = True`, so parallel agents don't wait on each other.
- `main(argv)`:
  1. Exit 2 with a usage line unless it gets exactly one argument, the socket path.
  2. If the path exists:
     - If it isn't a socket, exit 1 with a message.
     - If a connect to it succeeds, another broker is live. Exit 1 with `"another pi token broker is listening at <path>; is a container session for this repo already open?"`.
     - Otherwise it is stale: unlink it.
  3. Set `os.umask(0o077)` before binding. After binding, `os.chmod(path, 0o600)`.
  4. Install SIGTERM and SIGINT handlers that call `server.shutdown()` from a thread, or raise `SystemExit` around `serve_forever`. On exit, unlink the socket in a `finally`.
  5. Print nothing on success. Never print or log a token anywhere. stderr carries only startup errors.
- Executable bit on the template file (mode 755), like `build.sh` and `run.sh`. Check whether `install.py` copies the mode, since `test_container_is_stamped_and_runnable` checks `os.access` for the scripts. Mirror whatever it does for `build.sh`.

### 2. `.claude/skills/sssf/templates/container/Containerfile`, `build.sh` and `run.sh`

**`Containerfile`.** Add `xz-utils` to the apt list. After the `just` line, add:

- An `ARG NODE_VERSION=24.11.1` (major 24, the version `scripts/e2b_template.py` uses) and an `ARG PI_VERSION=1.4.0`, each with a comment. Node major 24 matches the host. pi 1.4.0 is the tool check's minimum (`sandbox_remote.py:63`). Bump both by editing these lines.
- A `RUN` that maps `dpkg --print-architecture` (`amd64`→`x64`, `arm64`→`arm64`; fail on anything else) and unpacks `https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.xz` into `/usr/local` with `--strip-components=1`.
- `RUN npm install -g @earendil-works/pi-coding-agent@${PI_VERSION} && pi --version`.
- Update the header comment. It no longer needs to tell the repo to add node.

**`build.sh`.** Compute `hash=$(sha256sum "$here/Containerfile" | cut -d' ' -f1)` and pass `--label "sssf.containerfile.sha256=$hash"` to `podman build`.

**`run.sh`:**

- Replace `podman image exists "$image" || "$here/build.sh"` with a staleness check:
  ```
  want=$(sha256sum "$here/Containerfile" | cut -d' ' -f1)
  have=$(podman image inspect --format '{{ index .Labels "sssf.containerfile.sha256" }}' "$image" 2>/dev/null || true)
  [ "$have" = "$want" ] || "$here/build.sh"
  ```
  Add a comment: an image built before the label, or before the Containerfile changed, is rebuilt (decision 15).
- Broker, after the token-file check and the image check:
  ```
  runtime=${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set; rootless podman needs it}
  sock="$runtime/sssf-pi-$(basename "$repo").sock"
  python3 "$here/pi_token_broker.py" "$sock" &
  broker=$!
  trap 'kill "$broker" 2>/dev/null || true' EXIT
  ```
  Then wait up to about 5 s for `[ -S "$sock" ]`, polling every 0.1 s. If the socket never appears, or the broker has exited (`kill -0 "$broker"` fails), exit 1 with `"the pi token broker didn't start; see the message above"`. Add a comment that a Claude Code roster never uses the broker, and that it starts whether or not the host is signed in to pi (decision 11).
- Change the final `exec podman run` to a plain `podman run`, so the `EXIT` trap runs and stops the broker. Leave a comment saying why there is no `exec`. Add these arguments:
  - `--network pasta:-T,43190`, with a comment: the container's `127.0.0.1:43190` reaches the host's obs server (decision 13).
  - `-v "$sock:/run/sssf/pi.sock"`
  - `-e SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock`
- Do not add `OBS_DISABLE` and do not filter `OBS_AUTH_TOKEN`.
- The onboarding `podman run … python3 -c` step stays as it is.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` (guard code)

Add near `vm_token`:

- `CONTAINER_SOCKET_VAR = "SSSF_PI_TOKEN_SOCKET"`.
- `PI_AUTH_JSON = Path(os.environ.get("PI_AUTH_PATH", str(Path.home() / ".pi" / "agent" / "auth.json")))`. This is a module constant, so tests can monkeypatch it, in the style of `MODELS_JSON`.
- `BROKER_TIMEOUT = 150`. That is more than the broker's 120 s pi timeout.
- `check_stored_login(path: Path = None)`: a missing file passes. Otherwise read it with `json.loads`. If the read fails (OSError or JSON error), or the result isn't a dict, raise `sandbox.SandboxFailed(f"SSSF can't read {path} to check it for a stored ChatGPT login; fix or remove it.")`. If `"openai" in data`, raise `sandbox.SandboxFailed(f"{path} has a ChatGPT login stored in the container (an openai entry). pi would use it instead of the host's sign-in, so remove that entry (or the file) and start again; the container gets its token from the host.")`. Never put values in a message.
- `container_token(socket_path: str) -> str`: open `socket.socket(AF_UNIX, SOCK_STREAM)` with `settimeout(BROKER_TIMEOUT)`, connect, and read to EOF, capped at 64 KiB. Then decode it as UTF-8 and take the first line.
  - On `OSError`, including a timeout, raise `SandboxFailed(f"the pi token broker at {socket_path} isn't answering ({type(e).__name__}); start the session with container/run.sh, which runs it on the host.")`.
  - On a line starting with `ERR `, raise `SandboxFailed(line[4:])`.
  - On a line starting with `OK ` with a non-empty rest, return the rest stripped.
  - On anything else, raise `SandboxFailed(f"the pi token broker at {socket_path} gave no token.")`.
  - Defence in depth: if the token starts with `sk-`, raise the same API-key refusal `vm_token` uses, worded for the container.
  - Never put a token in a message.
- `_in_container(env, tree) -> env`: return `env` unchanged unless `tree is sandbox.LOCAL` and `os.environ.get(CONTAINER_SOCKET_VAR)`. Otherwise call `check_stored_login()`, then set `env["OPENAI_API_KEY"] = container_token(socket)`. Do not touch `OBS_*`.
- In `run()`, change the start line to:
  ```
  env = _in_vm(env, tree, request.refuse_rules)
  env = _in_container(env, tree)
  process = tree.start(cmd, cwd=request.cwd, env=env)
  ```
  so both happen before the process starts, and a `SandboxFailed` raised there stops the call before pi runs. `SandboxFailed` is already in `session.STOPS`, so the run ends with the message and exit 1, not a traceback.
- Add `import socket`. Update the module docstring, or `_in_vm`'s, with one sentence on the container route.

`run()` reads the socket path from `os.environ`, not from `child_env`, which is the operator's environment. `child_env` with `SSSF_PI_AUTH=subscription` still pops `OPENAI_API_KEY` first, and `_in_container` then sets the broker's token.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py` and `session.py` (guard code)

**`sandbox.py`.** Add `extensions_in_repo(cfg, root: Path) -> None`, next to `extensions_in_clone`.

- For every agent with `coding_agent == "pi"` and each entry `p` in its `harness_engineering`:
  - Take `expanded = os.path.expanduser(p)`.
  - Take `full = Path(os.path.normpath(expanded if Path(expanded).is_absolute() else root / expanded))`.
  - If `full.relative_to(root)` raises `ValueError`, raise `SystemExit(f"{agent.name}'s harness_engineering extension {p} is outside the repo ({root}), and the operator container mounts only the repo, so pi can't load it there. Move it into the repo, or start the session on the host.")`.
- Don't check git or existence: uncommitted paths inside the repo are fine (decision 14).
- Docstring: cite decision 14 of `docs/designs/container-operator.md`.

**`session.py` `ensure`.** After the `if sandbox_name:` block and before `with worktree.launching():`, add:

```
elif os.environ.get("SSSF_PI_TOKEN_SOCKET"):
    sandbox.extensions_in_repo(cfg, worktree.main_checkout())   # the operator container (decision 14)
```

It runs only when no VM was chosen. Make it an `elif` on the same `if sandbox_name:`, so a VM run keeps `extensions_in_clone` and only that. A Claude Code roster has no pi agents, so the loop finds nothing. `os` is already imported in `session.py`; check that, and add the import if it isn't.

### 5. `.claude/skills/sssf/scripts/install.py`

- Delete the `if args.container:` block that reads the roster and calls `parser.error(...)` (lines 546-551).
- Update the module docstring (line 16) and the `--container` help text (line 526): drop "(Claude Code rosters only)".
- In the "container only" next steps (around line 556) and in the post-install steps that print `container/run.sh` (around lines 594-597), add one line: `a pi roster also needs the host signed in to pi: run `pi`, then /login (openai)`. Add nothing else.

### 6. Docs the change makes out of date

- `.claude/skills/sssf/cookbooks/install.md`, section "Run the operator session in a container":
  - Replace line 146's "It needs Podman and a Claude Code roster. The image has no Pi." with: it needs Podman, and a pi roster also needs the host signed in to pi (`pi`, then `/login`, `openai`). The host keeps the sign-in, and each pi agent gets a one-hour token from it.
  - In the file table, the `Containerfile` row gains node 24 and pi 1.4.0, pinned. Add a row for `container/pi_token_broker.py`: run on the host by `run.sh`, it hands each pi agent a one-hour ChatGPT token through a socket.
  - The `run.sh` row says it rebuilds the image when the Containerfile changed.
  - Add one sentence that obs events from the container show on the host dashboard (`127.0.0.1:43190` is forwarded).
  - Add one sentence that a pi extension must be inside the repo.
  - Add: if a run stops on a stored ChatGPT login, delete the `openai` entry from `~/.pi/agent/auth.json` inside the container.
- `.claude/skills/sssf/references/config.md`: add `### pi in the operator container` under "## Harness engineering", after "### pi extensions in a VM". It covers:
  - how the token is handed over (`SSSF_PI_TOKEN_SOCKET`, `OPENAI_API_KEY` per call);
  - that `harness_engineering` paths must be inside the repo, committed or not, or the run stops before it starts;
  - that an `openai` entry in the container's `~/.pi/agent/auth.json` stops the run;
  - that obs works as on the host (no `OBS_DISABLE`, `OBS_AUTH_TOKEN` kept);
  - **Known limit:** any process in the container can get a one-hour token from the socket while the session is open (decision 9).
- `.claude/skills/sssf/cookbooks/onboard.md` line 44: drop "It needs a Claude Code roster." In its place, say a pi roster needs the host signed in to pi.
- `README.md` line 106: replace "It works with Claude Code rosters only." with "A pi roster needs the host signed in to pi."
- Docstrings: `agent_pi._in_vm`, the new functions, and `sandbox.extensions_in_clone`'s mention of `extensions_in_repo`, if it helps.
- No CHANGELOG: the repo keeps none.
- Do not edit `docs/designs/container-operator.md` or anything in `app_docs/`.
- For the documenter: any `app_docs/` write-up on the operator container that says "Claude Code rosters only", or that the image has no pi, now describes old behaviour.

### 7. Tests (all under `.claude/skills/sssf/tests/`, none needs Podman or a real pi sign-in)

**`tests/test_container.py`:**

- Replace `test_a_pi_roster_cannot_have_the_container` with `test_a_pi_roster_can_have_the_container`: `install("--container")` succeeds, every file in `FILES` exists, and the output mentions signing the host in to pi.
- Add `"container/pi_token_broker.py"` to `FILES`. The stamped/executable test then covers it. If the broker isn't meant to be executable, keep it out of the `os.access` assertion.
- Add text checks on the template files. The Containerfile:
  - pins `PI_VERSION=1.4.0`;
  - has `@earendil-works/pi-coding-agent@${PI_VERSION}`;
  - has a `NODE_VERSION=24.` value.

  `build.sh` has `sssf.containerfile.sha256`. `run.sh` has:
  - `pi_token_broker.py`, `trap`, `/run/sssf/pi.sock`, `SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock`, `pasta:-T,43190` and `sssf.containerfile.sha256`;
  - no `exec podman run` and no `OBS_DISABLE`.
- Add `bash -n` on `run.sh` and `build.sh`.

**`tests/test_pi_token_broker.py` (new).** Import the broker by path with `importlib.util.spec_from_file_location` from `templates/container/pi_token_broker.py`. Make a fake `pi` script on a tmp `PATH`, or set `PI_PATH`. Start `main([sock])` in a subprocess with `sys.executable`, using a tmp dir socket path, which must be short: use `tempfile.mkdtemp(dir="/tmp")`. Assert:

- The socket mode is `0o600`.
- With a fake pi that prints `eyJ-access`, a client gets `OK eyJ-access`. The fake pi records its argv to a file, and the test asserts it is exactly `auth print-bearer-token --provider openai --min-expiry 35m`.
- A fake pi that prints `sk-live` gives `ERR` naming an API key, without the token.
- A fake pi that exits 1 gives `ERR` with "Sign in with `pi`".
- A missing pi gives `ERR` with "not installed".
- The broker's stdout and stderr never contain the token.
- A second broker on the same live socket exits 1 and leaves the first answering.
- A stale socket file is replaced.
- SIGTERM removes the socket.

**`tests/test_agent_pi.py` (add):**

- A fake broker (a thread with `socket.AF_UNIX`, one-line answer) and `monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", path)`:
  - `_in_container(env, sandbox.LOCAL)` sets `OPENAI_API_KEY` to the token and leaves `OBS_AUTH_TOKEN` and the absence of `OBS_DISABLE` alone.
  - Under `SSSF_PI_AUTH=subscription`, `child_env` followed by `_in_container` still gives the broker token.
- `ERR no sign-in` raises `sandbox.SandboxFailed` with that message. A missing socket raises `SandboxFailed` with "isn't answering". `OK sk-x` raises without the token in the message.
- Without the variable, `_in_container` returns `env` unchanged, the same dict contents (host run).
- With a remote tree (any object that isn't `sandbox.LOCAL`), `_in_container` doesn't touch the env (VM run unchanged).
- `check_stored_login`:
  - passes on a missing file and on `{"anthropic": {...}}`;
  - raises on `{"openai": {"access": "SECRET"}}`, and `"SECRET"` is not in the message;
  - raises on invalid JSON.

  Use `monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", tmp)` or the argument.
- `run()` stops before pi starts: monkeypatch `sandbox.current()` to return a stub LOCAL-like tree whose `start` records calls. Simplest is to keep `sandbox.LOCAL` and monkeypatch `sandbox.LOCAL.start`, or `agent_pi.sandbox.current` returning `sandbox.LOCAL` with `start` patched. Then point the socket env at a fake broker answering `ERR …`, or plant an `openai` auth.json, and assert `SandboxFailed` is raised and `start` was never called. Also monkeypatch `resolve_model` and `context_window`, as the existing run tests do.

**`tests/test_sandbox_extensions.py` (add), using the existing `shipped()` and `roster()` helpers built from `templates/sssf.config.yaml`:**

- `extensions_in_repo(cfg, root)` passes on the shipped roster's `adws/adw_data/harness_engineering/subagents.ts` (relative, inside), on an absolute path inside `root`, and on an uncommitted file inside.
- It raises `SystemExit` naming `builder` and the path for `/tmp/elsewhere/x.ts`, for `~/.pi/agent/extensions/x.ts` (monkeypatch `HOME` to a dir outside `root`), and for `../outside.ts`.
- A shipped Claude Code roster (`templates/sssf.config.claude_code.yaml`) with no pi agents passes.
- `session.ensure` wiring:
  - With `SSSF_PI_TOKEN_SOCKET` set and an outside path, `ensure(cfg)` raises `SystemExit` before `worktree.launching` is entered. Monkeypatch `worktree.launching` to fail the test if called, and `worktree.main_checkout` to return `root`.
  - With the variable unset, `extensions_in_repo` is not called (host unchanged).
  - With `sandbox_name` set, it is not called either. Monkeypatch `sandbox.prepare` to a no-op and `sandbox.extensions_in_repo` to record calls.

## For the engineer

- The real proof in a container (decision 16), after the merge, by hand in sssf-sandbox from `container/run.sh` with a pi roster:
  1. The marker extension loads.
  2. The refuse rule answers "SSSF refused this command".
  3. An obs event from the run shows on the host dashboard.
  4. The host's own `pi` is still signed in afterward.
  5. A Claude Code roster runs as before.
- The hub's own stamped copy (`adws/adw_modules/`, protected) picks up the `agent_pi.py`, `sandbox.py`, and `session.py` changes through `install.py --update` when you choose to run it.

## Verification

Run from the repo root.

1. The hub suite (the repo's `test` quality block) runs all of the tests above:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. The focused files, for a quick loop:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_container.py tests/test_pi_token_broker.py tests/test_agent_pi.py tests/test_sandbox_extensions.py tests/test_sandbox_remote.py -q -p no:cacheprovider`
   `test_sandbox_remote.py` must pass unchanged: that is the proof that the VM path is untouched.
3. `bash -n .claude/skills/sssf/templates/container/run.sh && bash -n .claude/skills/sssf/templates/container/build.sh`
4. `grep -rn "Claude Code rosters only\|the image has no Pi" .claude/skills/sssf README.md` finds nothing outside `docs/designs/`.
5. `git diff --stat a0d1af9 -- adws/ app_docs/ docs/designs/` is empty.

No new tool dependency. `python3`, `sha256sum` and `podman` with pasta are already assumed on the host by `run.sh`.
