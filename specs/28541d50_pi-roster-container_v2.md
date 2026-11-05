# Plan: let a pi roster run in the operator container

This plan builds decisions 8 to 16 of `docs/designs/container-operator.md`, in its section "pi in the container". Those decisions are the source of the bar. Every path below is from the repo root. All code changes are in the skill, under `.claude/skills/sssf/`. Nothing under `adws/`, the hub's own stamped copy, changes.

## Baseline

`main` at `a0d1af9` ("Settle pi in the container in container-operator.md"). Every finding is judged against that commit. At the baseline:

- `install.py --container` refuses any roster without `coding_agent: claude_code` (`.claude/skills/sssf/scripts/install.py:546-551`).
- The image has no node or pi. `run.sh` passes in only the Claude token. It `exec`s `podman run` on Podman's default network, and it rebuilds the image only when the image is missing.
- `agent_pi.run` passes the operator's environment to a LOCAL tree unchanged. Only a remote tree gets a token (`_in_vm` → `vm_token`).

## Threat model

Inside the container, agents run as the engineer's own uid (`--userns=keep-id`). Claude Code runs with `--dangerously-skip-permissions`, and pi in its bypass mode.

An agent can:

- read and write everything the container mounts: the repo with its `.env`, `~/.config/sssf`, the `sssf-operator-home` volume, and the pi token socket;
- open any network connection the container has, including the forwarded `127.0.0.1:43190`.

An agent can't:

- change the operator's process environment, so it can't change `SSSF_PI_TOKEN_SOCKET` for later agent calls;
- reach the host's `~/.pi/agent`, which holds the 30-day refresh token and every other pi credential, because it isn't mounted.

What this guard can't stop at its level:

- Any process in the container can connect to the socket and mint a one-hour ChatGPT access token while the session is open. Decision 9 records this as a known limit: everything there runs as one user, so a socket secret would be one agents could read.
- An agent can read the token from its own `OPENAI_API_KEY`, as on the host and in a VM.
- An agent can write a `~/.pi/agent/auth.json` into the home volume. The check catches an `openai` entry before the next pi agent call. It doesn't catch other providers or other pi settings files.
- An agent can edit an extension inside the repo, and the edited one loads for the next call.

## Bar

The change must:

1. Hand each pi agent call in the container a fresh token from the host broker as `OPENAI_API_KEY`. This happens only when `SSSF_PI_TOKEN_SOCKET` is set and the tree is local (`sandbox.LOCAL`).
2. Stop a pi agent call before pi starts when the broker answers with an error, can't be reached, or gives no answer. The stop carries the broker's message.
3. Stop a pi agent call before pi starts when the container's `~/.pi/agent/auth.json` has a top-level `openai` key, or can't be read as a JSON object. The stop names the file and says to remove the entry. Only key names are read. Values are never logged, printed, or put in a message.
4. Stop a container run before it starts when a pi agent's `harness_engineering` path is outside the repo, `~` paths included. No trace rows or worktree are made. The message names the agent and the path. A path inside the repo passes, committed or not.
5. Keep the refresh token on the host. The broker never logs or prints a token, and it refuses a credential that starts with `sk-`.
6. Keep the guard code's behaviour exactly as at the baseline for three kinds of run: a host run (no `SSSF_PI_TOKEN_SOCKET`), a VM run (`--sandbox vercel|e2b`, launched from anywhere), and a Claude Code roster in the container.
   - `_in_vm` and `vm_token` are unchanged.
   - `agent_pi._in_container` and `sandbox.extensions_in_repo` change nothing when no pi agent runs or no pi agent is checked.
   - In the container, no `OBS_DISABLE` is set and `OBS_AUTH_TOKEN` is kept.
7. Apply these `run.sh` and `build.sh` changes to every roster in the container, Claude Code included. They are intended, not a regression:
   - The broker starts, and `run.sh` exits 1 if it doesn't come up (decision 11).
   - `XDG_RUNTIME_DIR` must be set.
   - The network is `--network pasta:-T,43190` (decision 13), so a Podman without pasta can't start a session.
   - The final `podman run` is no longer `exec`'d, so the `EXIT` trap can stop the broker.
   - The image is rebuilt when its Containerfile-hash label doesn't match (decision 15). The first start after the update rebuilds every existing image, since none has the label.

   A Claude Code agent never talks to the broker.

It must never:

- Mount the host's `~/.pi`, or any file in it, into the container.
- Keep an agent's file, run agent code on the host, or delete or overwrite an engineer's file. The baseline does none of these. The broker runs only `pi auth print-bearer-token …` with a fixed argv and reads nothing from the client. The only file `run.sh` removes is its own socket, `$XDG_RUNTIME_DIR/sssf-pi-<repo>.sock`.
- Make a Claude Code agent call depend on the broker or on a host pi sign-in.

## Out of scope

These holes stay open, and a reviewer files them as old holes:

- **A VM run launched from inside the container.** `sandbox.prepare` and `_in_vm` still call `vm_token`, which runs the container's own `pi` and fails without a sign-in. Nothing routes `vm_token` through the socket.
- **Minting tokens through the socket while the session is open** (decision 9). There is no rate limit and no per-agent secret.
- **Other stored logins.** An `auth.json` entry for a provider other than `openai` isn't caught, and neither is pi auth stored outside `~/.pi/agent/auth.json`, for example under a `PI_CODING_AGENT_DIR` override.
- **An extension edited inside the repo.** In the container, an agent can edit a `harness_engineering` extension inside the mounted repo, committed or not, and the edited one loads for the next agent call. This is the container version of the known limit in `.claude/skills/sssf/references/config.md:428`, and a host run has the same exposure, since an extension isn't a protected file. Nothing checks it.
- **A symlinked extension.** A `harness_engineering` path inside the repo can be a symlink to a file outside it. The check uses `os.path.normpath`, as `extensions_in_clone` does, and doesn't resolve links. Imports of an extension aren't followed either.
- **Podman without pasta**, such as podman 4 on slirp4netns. `run.sh` fails at `--network pasta:…` for every roster (Bar 7), and there is no fallback.
- **A broker killed with SIGKILL**, or a host crash. This can leave a stale socket file. The next `run.sh` replaces it, because the broker's `main()` unlinks a socket that nothing answers on.
- **The real-container proof** (decision 16). The engineer runs it by hand after the merge; see "For the engineer".

## Changes

### 1. `.claude/skills/sssf/templates/container/pi_token_broker.py` (new)

A stdlib-only Python 3 script. `run.sh` runs it on the host as `python3 pi_token_broker.py <socket path>`. It must work on Ubuntu 22.04's python3 (3.10), so use no 3.11+ syntax. It installs no signal handlers and doesn't unlink the socket on exit. `run.sh`'s trap does both: it kills the broker and removes the socket.

**Constants.** `MIN_EXPIRY = "35m"`, `PI = os.environ.get("PI_PATH", "pi")`, `PI_TIMEOUT = 120` (seconds).

**Protocol.** A client connects and sends nothing. The broker writes exactly one line, then closes the connection:

- `OK <token>\n` on success.
- `ERR <message>\n` on failure. The message is one line and never contains a token.

Document this in the module docstring, since the client in `agent_pi` (step 3) relies on it.

**`mint() -> tuple[bool, str]`.** It runs `[PI, "auth", "print-bearer-token", "--provider", "openai", "--min-expiry", MIN_EXPIRY]` with `capture_output=True, text=True, timeout=PI_TIMEOUT, check=False`. The result depends on what happens:

| What happens | Result |
| --- | --- |
| `FileNotFoundError` | `(False, "pi is not installed on the host, so the container can't get a ChatGPT token. Install pi on the host, sign in with `pi`, then /login.")` |
| `TimeoutExpired` | `(False, f"pi auth print-bearer-token on the host took longer than {PI_TIMEOUT}s.")` |
| Non-zero exit, or empty stdout | `(False, f"pi has no ChatGPT sign-in on the host to hand the container a token from (pi auth print-bearer-token --provider openai exited {code}). Sign in with `pi` on the host, then /login.")` |
| The token starts with `sk-` | `(False, "pi's openai credential on the host is an API key, not the ChatGPT sign-in, and SSSF doesn't put API keys in the container. Sign in to ChatGPT with `pi` on the host and /login.")` |
| Otherwise | `(True, token)`, with `token` the stripped stdout |

The no-sign-in message is worded like `vm_token`'s. Never include pi's stderr.

**Handler.** A `socketserver.BaseRequestHandler` that calls `mint()` and writes `OK …` or `ERR …`. It ignores `BrokenPipeError` and `ConnectionResetError`. The server is a `socketserver.ThreadingUnixStreamServer` with `daemon_threads = True`, so parallel agents don't wait on each other.

**`main(argv)`:**

1. With anything but exactly one argument, the socket path, exit 2 with a usage line.
2. If the path exists:
   - If it isn't a socket, exit 1 with a message.
   - If a connect to it succeeds, another broker is live: exit 1 with `"another pi token broker is listening at <path>; is a container session for this repo already open?"`. Leave that socket alone.
   - Otherwise it is stale: unlink it.
3. Call `os.umask(0o077)` before binding, then `os.chmod(path, 0o600)` after binding.
4. Call `server.serve_forever()`. `SIGTERM` from the trap ends the process.
5. Print nothing on success. Never print or log a token anywhere. stderr carries only startup errors.

**File mode.** The template file is mode 755, like `build.sh` and `run.sh`. Check how `install.py` copies the mode, since `test_container_is_stamped_and_runnable` checks `os.access` on the scripts, and mirror whatever it does for `build.sh`.

### 2. `.claude/skills/sssf/templates/container/Containerfile`, `build.sh` and `run.sh`

**`Containerfile`.** Add `xz-utils` to the apt list. After the `just` line:

- Add `ARG NODE_VERSION=24.11.1` (major 24, the version `scripts/e2b_template.py` uses) and `ARG PI_VERSION=1.4.0`, the tool check's minimum (`sandbox_remote.py:63`). Give each a comment saying to bump it by editing that line.
- Add a `RUN` that maps `dpkg --print-architecture` (`amd64`→`x64`, `arm64`→`arm64`, fail on anything else). It then unpacks `https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.xz` into `/usr/local` with `--strip-components=1`.
- Add `RUN npm install -g @earendil-works/pi-coding-agent@${PI_VERSION} && pi --version`.
- Update the header comment, which no longer needs to tell the repo to add node.

**`build.sh`.** Compute `hash=$(sha256sum "$here/Containerfile" | cut -d' ' -f1)` and pass `--label "sssf.containerfile.sha256=$hash"` to `podman build`.

**`run.sh`:**

1. Replace `podman image exists "$image" || "$here/build.sh"` with a check for a stale image:
   ```
   want=$(sha256sum "$here/Containerfile" | cut -d' ' -f1)
   have=$(podman image inspect --format '{{ index .Labels "sssf.containerfile.sha256" }}' "$image" 2>/dev/null || true)
   [ "$have" = "$want" ] || "$here/build.sh"
   ```
   Add a comment: an image built before the label, or before the Containerfile changed, is rebuilt (decision 15).
2. Start the broker after the token-file check and the image check:
   ```
   runtime=${XDG_RUNTIME_DIR:?XDG_RUNTIME_DIR is not set; rootless podman needs it}
   sock="$runtime/sssf-pi-$(basename "$repo").sock"
   python3 "$here/pi_token_broker.py" "$sock" &
   broker=$!
   trap 'kill "$broker" 2>/dev/null; rm -f "$sock"' EXIT
   ```
   Wait up to about 5 s for `[ -S "$sock" ]`, polling every 0.1 s. If the socket never appears, or the broker has already exited (`kill -0 "$broker"` fails), exit 1 with `"the pi token broker didn't start; see the message above"`.

   That exit runs the trap. If the broker failed because another session's broker is live, the trap's `rm -f` would remove the live session's socket. To avoid that, set the trap only after the socket appears and `kill -0` succeeds. Before then, on failure, run `kill "$broker" 2>/dev/null` directly and leave `$sock` alone.

   Add a comment: a Claude Code agent never uses the broker, and it starts whether or not the host is signed in to pi (decision 11).
3. Change the final `exec podman run` to a plain `podman run`, so the `EXIT` trap runs and stops the broker. Leave a comment saying why there is no `exec`. Add:
   - `--network pasta:-T,43190`, with a comment: the container's `127.0.0.1:43190` reaches the host's obs server (decision 13).
   - `-v "$sock:/run/sssf/pi.sock"`
   - `-e SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock`

   Don't add `OBS_DISABLE`, and don't filter `OBS_AUTH_TOKEN`.

The onboarding `podman run … python3 -c` step stays as it is.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` (guard code)

Add these next to `vm_token`:

- `CONTAINER_SOCKET_VAR = "SSSF_PI_TOKEN_SOCKET"`.
- `PI_AUTH_JSON = Path(os.environ.get("PI_AUTH_PATH", str(Path.home() / ".pi" / "agent" / "auth.json")))`. It is a module constant, so tests can monkeypatch it, as `MODELS_JSON` works.
- `BROKER_TIMEOUT = 150`, more than the broker's 120 s pi timeout.
- **`check_stored_login(path: Path = None)`:**
  - A missing file passes.
  - Otherwise, `json.loads` the file. If that fails (OSError or a JSON error), or the result isn't a dict, raise `sandbox.SandboxFailed(f"SSSF can't read {path} to check it for a stored ChatGPT login; fix or remove it.")`.
  - If `"openai" in data`, raise `sandbox.SandboxFailed(f"{path} has a ChatGPT login stored in the container (an openai entry). pi would use it instead of the host's sign-in, so remove that entry (or the file) and start again; the container gets its token from the host.")`.
  - Never put values in a message.
- **`container_token(socket_path: str) -> str`:**
  - Open a `socket.socket(AF_UNIX, SOCK_STREAM)` with `settimeout(BROKER_TIMEOUT)`, connect, and read to EOF, capped at 64 KiB. Decode it as UTF-8 and take the first line.
  - On `OSError`, a timeout included, raise `SandboxFailed(f"the pi token broker at {socket_path} isn't answering ({type(e).__name__}); start the session with container/run.sh, which runs it on the host.")`.
  - A line starting with `ERR ` raises `SandboxFailed(line[4:])`.
  - A line starting with `OK ` with a non-empty rest returns the rest, stripped.
  - Anything else raises `SandboxFailed(f"the pi token broker at {socket_path} gave no token.")`.
  - As a second guard, a token starting with `sk-` raises the same API-key refusal `vm_token` uses, worded for the container.
  - Never put a token in a message.
- **`_in_container(env, tree) -> env`:**
  - Return `env` unchanged unless `tree is sandbox.LOCAL` and `os.environ.get(CONTAINER_SOCKET_VAR)` is set.
  - Otherwise, call `check_stored_login()`, then set `env["OPENAI_API_KEY"] = container_token(socket)`.
  - Don't touch `OBS_*`.

In `run()`, change the start line to:

```
env = _in_vm(env, tree, request.refuse_rules)
env = _in_container(env, tree)
process = tree.start(cmd, cwd=request.cwd, env=env)
```

Both happen before the process starts, so a `SandboxFailed` raised there stops the call before pi runs. `SandboxFailed` is already in `session.STOPS`, so the run ends with the message and exit 1, not a traceback.

Also add `import socket`, and add one sentence on the container route to the module docstring or to `_in_vm`'s docstring.

The socket path comes from `os.environ`, the operator's environment, not from `child_env`. With `SSSF_PI_AUTH=subscription`, `child_env` still drops `OPENAI_API_KEY` first, and `_in_container` then sets the broker's token.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py` and `session.py` (guard code)

**`sandbox.py`.** Add `extensions_in_repo(cfg, root: Path) -> None`, next to `extensions_in_clone`:

- It checks every agent with `coding_agent == "pi"`, and each entry `p` in its `harness_engineering`.
- Expand and resolve each entry:
  - `expanded = os.path.expanduser(p)`
  - `full = Path(os.path.normpath(expanded if Path(expanded).is_absolute() else root / expanded))`
- If `full.relative_to(root)` raises `ValueError`, raise `SystemExit(f"{agent.name}'s harness_engineering extension {p} is outside the repo ({root}), and the operator container mounts only the repo, so pi can't load it there. Move it into the repo, or start the session on the host.")`.
- Check neither git nor whether the file exists: uncommitted paths inside the repo are fine (decision 14).
- In the docstring, cite decision 14 of `docs/designs/container-operator.md`.

**`session.py` `ensure`.** After the `if sandbox_name:` block, and before `with worktree.launching():`, add:

```
elif os.environ.get("SSSF_PI_TOKEN_SOCKET"):
    sandbox.extensions_in_repo(cfg, worktree.main_checkout())   # the operator container (decision 14)
```

This is an `elif` on that same `if sandbox_name:`, so it runs only when no VM was chosen, and a VM run keeps `extensions_in_clone` and only that. A Claude Code roster has no pi agents, so the loop finds nothing. Check whether `os` is already imported in `session.py`, and add the import if not.

### 5. `.claude/skills/sssf/scripts/install.py`

- Delete the `if args.container:` block that reads the roster and calls `parser.error(...)` (lines 546-551).
- Drop "(Claude Code rosters only)" from the module docstring (line 16) and from the `--container` help text (line 526).
- Two places print steps that name `container/run.sh`: the "container only" next steps (around line 556) and the post-install steps (around lines 594-597). Add this one line to each, and nothing else: `a pi roster also needs the host signed in to pi: run `pi`, then /login (openai)`.

### 6. Docs the change makes out of date

**`.claude/skills/sssf/cookbooks/install.md`, "Run the operator session in a container":**

- Replace line 146, "It needs Podman and a Claude Code roster. The image has no Pi.", with text that says:
  - it needs Podman;
  - a pi roster also needs the host signed in to pi (`pi`, then `/login`, then `openai`);
  - the host keeps the sign-in, and each pi agent gets a one-hour token from it.
- File table:
  - The `Containerfile` row gains node 24 and pi 1.4.0, both pinned.
  - Add a row for `container/pi_token_broker.py`: `run.sh` runs it on the host, and it hands each pi agent a one-hour ChatGPT token through a socket.
  - The `run.sh` row says it rebuilds the image when the Containerfile changed.
- Add a sentence each for:
  - obs events from the container show on the host dashboard, because `127.0.0.1:43190` is forwarded;
  - a pi extension must be inside the repo;
  - a session needs Podman with pasta, which is the rootless default from Podman 5;
  - if a run stops on a stored ChatGPT login, delete the `openai` entry from `~/.pi/agent/auth.json` inside the container.

**`.claude/skills/sssf/references/config.md`.** Add `### pi in the operator container` under `## Harness engineering`, after `### pi extensions in a VM`. It covers:

- how the token is handed over (`SSSF_PI_TOKEN_SOCKET`, `OPENAI_API_KEY` per call);
- `harness_engineering` paths must be inside the repo, committed or not, or the run stops before it starts;
- an `openai` entry in the container's `~/.pi/agent/auth.json` stops the run;
- obs works as on the host (no `OBS_DISABLE`, and `OBS_AUTH_TOKEN` is kept).

Give it these known limits:

- **Known limit:** any process in the container can get a one-hour token from the socket while the session is open (decision 9).
- **Known limit:** as in a VM, an agent can edit an extension in the repo, and the edited one loads for the next agent call.

**Other docs:**

- `.claude/skills/sssf/cookbooks/onboard.md` line 44: drop "It needs a Claude Code roster." and say instead that a pi roster needs the host signed in to pi.
- `README.md` line 106: replace "It works with Claude Code rosters only." with "A pi roster needs the host signed in to pi."
- Docstrings: `agent_pi._in_vm` and the new functions. `sandbox.extensions_in_clone`'s docstring can mention `extensions_in_repo` if that helps.

**Not changed:**

- There is no CHANGELOG to update; the repo keeps none.
- Don't edit `docs/designs/container-operator.md` or anything in `app_docs/`.
- For the documenter: any `app_docs/` write-up on the operator container that says "Claude Code rosters only", or that the image has no pi, now describes old behaviour.

### 7. Tests (all under `.claude/skills/sssf/tests/`, and none needs Podman or a real pi sign-in)

**`tests/test_container.py`:**

- Replace `test_a_pi_roster_cannot_have_the_container` with `test_a_pi_roster_can_have_the_container`. It checks that `install("--container")` succeeds, that every file in `FILES` exists, and that the output mentions signing the host in to pi.
- Add `"container/pi_token_broker.py"` to `FILES`, so the stamped/executable test covers it. If the broker isn't meant to be executable, keep it out of the `os.access` assertion.
- Add text checks on the templates:
  - The Containerfile pins `PI_VERSION=1.4.0`, has `@earendil-works/pi-coding-agent@${PI_VERSION}`, and has a `NODE_VERSION=24.` value.
  - `build.sh` has `sssf.containerfile.sha256`.
  - `run.sh` has `pi_token_broker.py`, `trap 'kill "$broker" 2>/dev/null; rm -f "$sock"' EXIT`, `/run/sssf/pi.sock`, `SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock`, `pasta:-T,43190` and `sssf.containerfile.sha256`.
  - `run.sh` has no `exec podman run` and no `OBS_DISABLE`.
- Run `bash -n` on `run.sh` and on `build.sh`.

**`tests/test_pi_token_broker.py` (new):**

Setup:

- Make a fake `pi` script on a tmp `PATH`, or point `PI_PATH` at it. The fake records its argv to a file.
- Start the broker in a subprocess with `sys.executable`, running `templates/container/pi_token_broker.py <sock>`. The socket path must be short: put it in `tempfile.mkdtemp(dir="/tmp")`.
- Stop each broker in teardown with `terminate()`, and remove the tmp dir.

Assertions:

- The socket's mode is `0o600`.
- With a fake pi that prints `eyJ-access`, a client gets `OK eyJ-access`, and the recorded argv is exactly `auth print-bearer-token --provider openai --min-expiry 35m`.
- A fake pi that prints `sk-live` gets `ERR` naming an API key, and the reply doesn't contain the token.
- A fake pi that exits 1 gets `ERR` with "Sign in with `pi`".
- A missing pi gets `ERR` with "not installed".
- The broker's stdout and stderr never contain the token.
- A second broker on the same live socket exits 1, and the first still answers.
- A stale socket file, one that is bound and closed with no listener, is replaced and answers.

Nothing tests signal cleanup in the broker, because the broker has none. `test_container.py`'s check on the trap text covers socket removal.

**`tests/test_agent_pi.py` (add):**

The token route, with a fake broker (a thread on a `socket.AF_UNIX` socket that answers one line) and `monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", path)`:

- `_in_container(env, sandbox.LOCAL)` sets `OPENAI_API_KEY` to the token. It leaves `OBS_AUTH_TOKEN` in place and adds no `OBS_DISABLE`.
- Under `SSSF_PI_AUTH=subscription`, `child_env` followed by `_in_container` still gives the broker's token.
- An `ERR no sign-in` answer raises `sandbox.SandboxFailed` with that message.
- A missing socket raises `SandboxFailed` with "isn't answering".
- An `OK sk-x` answer raises, and the message doesn't contain the token.

When it must change nothing:

- Without the variable, `_in_container` returns `env` with the same contents (a host run).
- With a remote tree, any object that isn't `sandbox.LOCAL`, `_in_container` doesn't touch the env (a VM run).

`check_stored_login`, with `monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", tmp)` or the argument:

- passes on a missing file and on `{"anthropic": {...}}`;
- raises on `{"openai": {"access": "SECRET"}}`, and `"SECRET"` isn't in the message;
- raises on invalid JSON.

`run()` stops before pi starts:

- Patch the tree's `start` to record calls: make `agent_pi.sandbox.current` return `sandbox.LOCAL` and patch `sandbox.LOCAL.start`.
- Patch `resolve_model` and `context_window`, as the existing run tests do.
- Point the socket variable at a fake broker that answers `ERR …`, or plant an `openai` entry in `auth.json`.
- Assert that `SandboxFailed` is raised and `start` was never called.

**`tests/test_sandbox_extensions.py` (add).** Use the existing `shipped()` and `roster()` helpers, which build from `templates/sssf.config.yaml`.

`extensions_in_repo(cfg, root)`:

- Passes on the shipped roster's `adws/adw_data/harness_engineering/subagents.ts` (relative, inside the repo), on an absolute path inside `root`, and on an uncommitted file inside the repo.
- Raises `SystemExit` naming `builder` and the path for `/tmp/elsewhere/x.ts`, for `~/.pi/agent/extensions/x.ts` (with `HOME` monkeypatched to a directory outside `root`), and for `../outside.ts`.
- Passes on the shipped Claude Code roster (`templates/sssf.config.claude_code.yaml`), which has no pi agents.

The `session.ensure` wiring. Patch `worktree.main_checkout` to return `root`, `sandbox.extensions_in_repo` to record calls where needed, and `sandbox.prepare` to a no-op:

- With `SSSF_PI_TOKEN_SOCKET` set and a path outside the repo, `ensure(cfg)` raises `SystemExit` before `worktree.launching` is entered. Patch `worktree.launching` to fail the test if it is called.
- With the variable unset, `extensions_in_repo` isn't called (the host is unchanged).
- With `sandbox_name` set, it isn't called either.

## For the engineer

- **The real proof in a container** (decision 16), after the merge. Run it by hand in sssf-sandbox from `container/run.sh` with a pi roster, and check:
  1. the marker extension loads;
  2. the refuse rule answers "SSSF refused this command";
  3. an obs event from the run shows on the host dashboard;
  4. the host's own `pi` is still signed in afterward;
  5. a Claude Code roster runs as before.
- **The hub's own stamped copy.** `adws/adw_modules/` is protected. It picks up the `agent_pi.py`, `sandbox.py`, and `session.py` changes through `install.py --update`, when you choose to run it.

## Verification

Run these from the repo root:

1. The hub suite, which is the repo's `test` quality block, covers every test above:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. The focused files, for a quick loop:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_container.py tests/test_pi_token_broker.py tests/test_agent_pi.py tests/test_sandbox_extensions.py tests/test_sandbox_remote.py -q -p no:cacheprovider`
   `test_sandbox_remote.py` must pass unchanged. It is the proof that the VM path is untouched.
3. `bash -n .claude/skills/sssf/templates/container/run.sh && bash -n .claude/skills/sssf/templates/container/build.sh`
4. `grep -rn "Claude Code rosters only\|the image has no Pi" .claude/skills/sssf README.md` finds nothing outside `docs/designs/`.
5. `git diff --quiet a0d1af9 -- adws/ app_docs/ docs/designs/` exits 0.

There is no new tool dependency: `run.sh` already assumes `python3`, `sha256sum`, and `podman` on the host, and now needs Podman's pasta network.
