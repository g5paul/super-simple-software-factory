# Close the pi-in-container review's broken promises

## Summary

Run 28541d50's bypass review (`adws/adw_data/sessions/28541d50/context_handoff/guard_report.md`)
found three broken promises in the pi-in-container work on `guard-pi-container`. This change
closes them, and adds five known limits to `references/config.md`:

1. `agent_pi.PI_AUTH_JSON` stops honouring `PI_AUTH_PATH`. It follows the same rule pi uses:
   `$PI_CODING_AGENT_DIR/auth.json` when that is set, else `~/.pi/agent/auth.json`.
2. `agent_pi.check_stored_login` never blocks. It reads the path only when it resolves to a
   regular file, and stops, naming the path, when anything else is there.
3. `container/run.sh` removes a socket left at the broker's path before it starts the broker,
   then waits until a connection succeeds, not just until the file exists.

Everything else on `guard-pi-container` stays as it is. A host run, a VM run, and a Claude Code
roster in the container behave exactly as they do today.

## Baseline

`guard-pi-container` at commit `5915fbf` ("Say in the README that VM runs work with pi rosters
too"). Every finding is judged against that commit. The work branch `guard-pi-container-fixes`
starts from it.

## Threat model

- The agent runs as the engineer's own user. In the operator container that is the same uid,
  through `--userns=keep-id`. It can write anything in the repo, gitignored files like `.env`
  included (they are never rolled back), the container's home volume (so
  `~/.pi/agent/auth.json` there), `/tmp`, and it can connect to `/run/sssf/pi.sock`. On a host
  run it can also write the engineer's `$XDG_RUNTIME_DIR`.
- `utils.py` runs `load_dotenv()` at import, before `agent_pi`'s module body runs. A line an
  agent writes to `.env` therefore lands in the next ADW's `os.environ`, and in `PI_AUTH_JSON`
  and the environment pi gets through `child_env()`.
- What this guard can't stop at its level, since the agent is the engineer's user: swapping
  `auth.json` between the check and pi's own read; pointing `PI_CODING_AGENT_DIR` (through
  `.env`) at a directory it controls, whose settings and extensions pi then loads; racing
  `run.sh` between its `rm` of the socket and the broker's `bind`; any process in the
  container connecting to the socket as often as it likes.
- The check's job is narrower: what pi will read as its stored `openai` login, the check reads
  too, and the check returns, pass or stop, in bounded time.

## Bar

Against `5915fbf`, the change must:

- Make `check_stored_login` read the `auth.json` pi itself reads: `PI_AUTH_PATH` no longer
  moves it, and `PI_CODING_AGENT_DIR` (with `~` expanded) does, as it moves pi.
- Make `check_stored_login` return in bounded time whatever sits at the path: a FIFO, a socket,
  a device, a directory, a symlink to any of these, a missing file, a dangling symlink.
  Anything there that isn't a regular file, after following symlinks with `stat` (never
  `open`), stops the run with a message naming the path. A missing file, or a dangling
  symlink, passes, as now.
- Make `run.sh` exit 1, before any `podman run`, when its own broker never answers, even with a
  socket left at the path by an earlier session.

It must never, where the baseline does otherwise:

- keep an agent's file, or run agent code;
- delete or overwrite an engineer's file. `run.sh` removes the path only when it is a socket
  and not a symlink. A regular file, a directory, or a symlink there is left alone, and the
  broker's existing "exists and is not a socket" refusal then stops the session.
- change a host run, a VM run, or a Claude Code roster in the container. `check_stored_login`
  runs only from `_in_container`, so a host and VM run never reach it. The broker still starts
  whether or not the host can sign pi in (decision 11).
- print or log a token, or any value from `auth.json`.

## Out of scope

These stay open. A reviewer should file them as old holes.

- Gitignored files, `.env` among them, aren't rolled back, so an agent can still set
  `PI_CODING_AGENT_DIR`, `SSSF_PI_AUTH`, `CLAUDE_CODE_PATH` and the like for the next ADW. This
  change only makes the check follow `PI_CODING_AGENT_DIR` where pi does.
- Only an `openai` entry is checked. `openai-codex`, `OPENAI`, and pi's other stored logins
  pass.
- The check and pi read `auth.json` at different moments (TOCTOU).
- An extension path inside the repo that is a symlink to a file outside it passes
  `sandbox.extensions_in_repo`, and imports aren't followed.
- Each connection to the socket starts one host `pi` process for up to 120 s, with no cap.
- `--sandbox vercel|e2b` launched inside the container mints VM tokens with the container's own
  pi (`vm_token`), not through the broker.
- A process racing `run.sh` between its `rm` and the broker's `bind` can still put its own
  listener at the path. On a host run that process is the engineer's own user.
- Quoted or split command words getting past `rules.breach` (config.md already says so).

## Files to change

All paths are from the repo root. None of them is a protected path.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

1. Add `import stat` with the other stdlib imports if it isn't there (check: `os`, `json`,
   `socket` already are).
2. Replace the `PI_AUTH_JSON = Path(os.environ.get("PI_AUTH_PATH", ...))` lines (around
   line 258) with:

   ```python
   def _pi_auth_json() -> Path:
       """Where pi reads its stored logins: auth.json in PI_CODING_AGENT_DIR when the
       operator's environment sets it (pi expands a leading ~), else ~/.pi/agent/auth.json
       (pi's config.js getAgentDir). No other override: the check must read what pi reads."""
       agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
       if agent_dir:
           return Path(os.path.expanduser(agent_dir)) / "auth.json"
       return Path.home() / ".pi" / "agent" / "auth.json"


   PI_AUTH_JSON = _pi_auth_json()   # tests monkeypatch this
   AUTH_JSON_LIMIT = 1 << 20        # bytes; pi's auth.json is a few KB
   ```

   It stays a module value, set at import. `.env` is loaded by then (`utils.py`), and pi gets
   the same `os.environ` through `child_env()`, so both read the same `PI_CODING_AGENT_DIR`.
   No other code reads `PI_AUTH_PATH`. Grep the skill to confirm, and leave none.

3. Rewrite `check_stored_login` so it never opens anything but a regular file and never blocks:

   ```python
   def check_stored_login(path: Path = None) -> None:
       """Stop if the container's pi auth.json holds an openai login: pi would use
       it instead of the host's sign-in. Reads key names only; never a value.

       Never blocks: the path is stat'ed (following a symlink, as pi does) before
       anything opens it, and only a regular file is read. Anything else there
       (FIFO, socket, device, directory) stops the run. A missing file passes."""
       path = path or PI_AUTH_JSON
       if not path.is_absolute():
           raise sandbox.SandboxFailed(
               f"PI_CODING_AGENT_DIR puts pi's auth.json at {path}, a relative path, so SSSF "
               "can't tell which file pi will read; set it to an absolute path.")
       try:
           info = os.stat(path)
       except (FileNotFoundError, NotADirectoryError):
           return
       except OSError:
           info = None
       if info is not None and not stat.S_ISREG(info.st_mode):
           raise sandbox.SandboxFailed(
               f"{path} is not a regular file, so SSSF can't check it for a stored ChatGPT "
               "login; remove it and start again.")
       data = None
       if info is not None:
           try:
               data = json.loads(_read_regular(path).decode("utf-8"))
           except (OSError, ValueError):
               data = None
       if not isinstance(data, dict):
           raise sandbox.SandboxFailed(f"SSSF can't read {path} to check it for a stored ChatGPT "
                                       "login; fix or remove it.")
       if "openai" in data:
           raise sandbox.SandboxFailed(...)   # the existing message, unchanged
   ```

   and add, next to it:

   ```python
   def _read_regular(path: Path) -> bytes:
       """The bytes of the regular file at path. O_NONBLOCK keeps a FIFO swapped in
       after the stat from holding the open, and fstat refuses anything but a
       regular file. More than AUTH_JSON_LIMIT bytes is an OSError."""
       fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
       try:
           if not stat.S_ISREG(os.fstat(fd).st_mode):
               raise OSError(f"{path} is not a regular file")
           chunks, size = [], 0
           while size <= AUTH_JSON_LIMIT:
               chunk = os.read(fd, 65536)
               if not chunk:
                   return b"".join(chunks)
               chunks.append(chunk)
               size += len(chunk)
           raise OSError(f"{path} is larger than {AUTH_JSON_LIMIT} bytes")
       finally:
           os.close(fd)
   ```

   `UnicodeDecodeError` is a `ValueError`, so bad bytes take the existing "can't read" stop.
   A permission error on `stat` or `open` takes it too, as an unreadable file does today.

### `.claude/skills/sssf/templates/container/run.sh`

Replace lines 31 to 43 (from `sock=` through the `fi` after the wait) with:

```bash
sock="$runtime/sssf-pi-$(basename "$repo").sock"
# A socket left by an earlier session would pass a file test even with this
# broker dead, so remove it first, and wait for a connection, not the file.
# Only a socket goes: a symlink or any other file there is left, and the
# broker refuses to start on it.
if [ -S "$sock" ] && [ ! -L "$sock" ]; then
    rm -f -- "$sock"
fi
python3 "$here/pi_token_broker.py" "$sock" &
broker=$!
trap 'kill "$broker" 2>/dev/null' EXIT
answered=""
for _ in $(seq 50); do
    kill -0 "$broker" 2>/dev/null || break
    if python3 -c 'import socket, sys
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(1)
s.connect(sys.argv[1])' "$sock" 2>/dev/null; then
        answered=1
        break
    fi
    sleep 0.1
done
if [ -z "$answered" ]; then
    echo "the pi token broker at $sock didn't answer; see the message above" >&2
    exit 1
fi
```

Keep the existing comment block above it (decision 11) and everything after it unchanged. The
probe only connects and closes. The broker runs one `pi auth print-bearer-token` for it and
discards the answer, as it does for its own stale-socket probe. A host without pi or without a
sign-in still starts the session, since the connection succeeds whatever the broker answers.

### `.claude/skills/sssf/templates/container/pi_token_broker.py`

Docstring only. Change "run.sh's trap only kills it, and the next start replaces a socket
nothing answers on." to say that run.sh's trap only kills it, run.sh removes a socket left at
the path before the next start, and the broker itself still replaces a socket nothing answers
on when it is started some other way. No code change.

### `.claude/skills/sssf/tests/test_agent_pi.py`

Keep every existing test. They already monkeypatch `agent_pi.PI_AUTH_JSON`. Add:

- `test_the_auth_path_follows_pi_and_ignores_PI_AUTH_PATH(tmp_path, monkeypatch)`:
  `setenv("HOME", tmp_path)`, `setenv("PI_AUTH_PATH", "/nonexistent/auth.json")`,
  `delenv("PI_CODING_AGENT_DIR", raising=False)`. Assert `agent_pi._pi_auth_json() ==
  tmp_path / ".pi/agent/auth.json"`. Then `setenv("PI_CODING_AGENT_DIR", str(tmp_path / "x"))`
  gives `tmp_path / "x/auth.json"`, and `"~/y"` gives `tmp_path / "y/auth.json"`. Assert
  `"PI_AUTH_PATH" not in Path(agent_pi.__file__).read_text()`.
- `test_a_relative_agent_dir_stops`: `check_stored_login(Path("rel/auth.json"))` raises
  `SandboxFailed` matching `absolute`.
- `test_a_non_regular_auth_json_stops_without_blocking` (parametrized over `fifo`,
  `fifo_link`, `dir`, `socket`, `device_link`): build the thing at `path`. Use `os.mkfifo`,
  `os.symlink(fifo, path)`, `path.mkdir()`, an AF_UNIX socket bound under a short
  `tempfile.mkdtemp(dir="/tmp")` (path length), and `os.symlink("/dev/null", path)`. Call
  `check_stored_login(path)` in a daemon `threading.Thread`, `join(timeout=5)`, assert it
  finished and raised `SandboxFailed` whose message contains `str(path)`. In cleanup, if the
  thread is still alive, unblock it with `os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)` wrapped
  in `try/except OSError`, then close the socket and remove the temp dir.
- `test_a_symlinked_auth_json_is_read_like_pi_reads_it`: a symlink to a regular file holding
  `{"openai": {"access": "SECRET"}}` stops, and `SECRET` is not in the message. A symlink to
  `{"anthropic": {}}` passes. A dangling symlink passes.
- `test_an_oversized_auth_json_stops`: monkeypatch `AUTH_JSON_LIMIT` to `16`, write
  `'{"a": "' + "x" * 64 + '"}'`, expect `SandboxFailed` matching `can't read`.

### `.claude/skills/sssf/tests/test_container.py`

1. In `test_the_scripts_rebuild_on_a_changed_containerfile_and_run_the_broker`, drop
   `rm -f "$sock"` and `rm "$sock"` from `banned`. Add `'[ -S "$sock" ] && [ ! -L "$sock" ]'`
   and `"s.connect(sys.argv[1])"` to the needles, and `'[ -S "$sock" ] && break'` to `banned`.
2. Add a behaviour test of the wait, which runs a copy of `run.sh` with stubs. Add a helper
   `_session(tmp_path, broker_source: str, at_sock)`:
   - `rt = tempfile.mkdtemp(dir="/tmp")`, a short `XDG_RUNTIME_DIR` for AF_UNIX. Remove it in a
     `finally` or fixture teardown.
   - `box = tmp_path / "box"`, `git init -q box`, `git -C box config user.name t` and
     `user.email t@t`. Make `box/container/` holding a copy of `TEMPLATES / "run.sh"`
     (executable), a `Containerfile` (`FROM scratch`), an executable `build.sh` that does
     `exit 0`, and `pi_token_broker.py` holding `broker_source`.
   - A `bin/podman` stub, `#!/bin/sh` plus `echo "$*" >> "$PODMAN_LOG"` plus `exit 0`, put
     first on `PATH`.
   - A token file holding `CLAUDE_CODE_OAUTH_TOKEN=x`, passed as `SSSF_CONTAINER_TOKEN`.
   - `PI_PATH=false` in the env, so the probe's mint never calls the real pi.
   - `sock = f"{rt}/sssf-pi-box.sock"`, and call `at_sock(sock)` to plant what the case needs.
   - Run `bash box/container/run.sh` with `stdin=DEVNULL`, `capture_output`, `timeout=60`.
     Return the result, the podman log lines, and `sock`.
   Cases:
   - `test_a_stale_socket_and_a_dead_broker_stop_the_session`: plant a stale socket (bind an
     AF_UNIX socket at `sock`, then close it; the file stays). Broker stub:
     `import time; time.sleep(60)`, which never binds. Expect exit 1, `didn't answer` in
     stderr, and no podman log line starting with `run`. At `5915fbf` this case reaches
     `podman run`, which is the review's finding.
   - `test_a_stale_socket_is_replaced_by_a_live_broker`: plant the same stale socket and use
     the real broker source (`(TEMPLATES / "pi_token_broker.py").read_text()`). Expect exit 0,
     a podman log line that starts with `run` and contains `-it`, and
     `stat.S_ISSOCK(os.lstat(sock).st_mode)`.
   - `test_a_regular_file_at_the_socket_path_is_kept`: plant a regular file holding `keep`,
     with the real broker. Expect exit 1, no `run` line, and the file still a regular file
     holding `keep`.

### `.claude/skills/sssf/references/config.md`

In `### pi in the operator container`:

1. Rewrite the sentence "An `openai` entry in the container's `~/.pi/agent/auth.json` also
   stops the run, because pi would use it instead of the host's sign-in." to: an `openai` entry
   in the `auth.json` pi reads in the container (`~/.pi/agent/auth.json`, or
   `$PI_CODING_AGENT_DIR/auth.json` when that is set) also stops the run, because pi would use
   it instead of the host's sign-in. So does anything at that path that isn't a regular file,
   or a relative `PI_CODING_AGENT_DIR`. Add one sentence to the first paragraph: `run.sh`
   removes a socket left at the broker's path, waits until the new broker answers, and exits 1
   if it doesn't.
2. Right after the existing "**Known limit:** any process in the container can get a one-hour
   token from the socket…" paragraph, add these five, each as its own
   `**Known limit:** …` paragraph:
   - The stored-login check looks for an `openai` entry only. Other stored logins pi keeps are
     not checked.
   - The check and pi read `auth.json` at different moments, so a file swapped between them
     can pass the check and still reach pi.
   - An extension path inside the repo that is a symlink to a file outside it passes the launch
     check, and imports aren't followed.
   - Each connection to the socket starts one host `pi` process, with no cap, so a process in
     the container can load the host.
   - `--sandbox vercel|e2b` launched from inside the container mints its VM tokens with the
     container's own pi, not through the broker.

   Leave the existing extension-edit limit where it is.

### `docs/designs/container-operator.md`

- Decision 10: after "SSSF checks the container's `~/.pi/agent/auth.json`", add "(or
  `$PI_CODING_AGENT_DIR/auth.json`, where pi reads it then)". Add that anything there but a
  regular file also stops the run, and the check never opens one.
- Decision 8: after "and stops it with a `trap` when the session ends", add one sentence:
  `run.sh` first removes a socket left at the path by an earlier session, and waits until a
  connection to the new broker succeeds, exiting 1 if none does.
- Add no new decision, and leave the log tables alone.

### Docs checked and left alone

- `README.md`, `.claude/skills/sssf/cookbooks/install.md`: they describe the broker and the
  sign-in, not the check's path or the wait. No change.
- No CHANGELOG in the repo.
- `app_docs/`: no write-up describes `PI_AUTH_PATH`, the stored-login check, or `run.sh`'s
  broker wait (grep for `auth.json` and `broker` finds none), so the documenter has nothing old
  to note. Don't edit `app_docs/`.
- `adws/adw_modules/agent_pi.py` (the hub's own installed copy) is protected and has no
  container code. Leave it.

## Verification

From the repo root:

1. The hub suite, as `quality.py` runs it. It must exit 0:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. The focused files, which must exit 0:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py tests/test_container.py tests/test_pi_token_broker.py -q -p no:cacheprovider`
3. `bash -n .claude/skills/sssf/templates/container/run.sh` exits 0. The container test also
   runs this.
4. `grep -rn PI_AUTH_PATH .claude/skills/sssf` finds nothing, so it exits 1.
5. By hand, to confirm: revert only `run.sh` to `5915fbf`
   (`git show 5915fbf:.claude/skills/sssf/templates/container/run.sh`, run against a temp copy,
   never by checking out over the work tree) and confirm
   `test_a_stale_socket_and_a_dead_broker_stop_the_session` fails against it. Do the same for
   the FIFO case against the baseline `check_stored_login`. Say what you saw in your report.

No new tool or dependency: `mkfifo`, AF_UNIX sockets, `git`, `bash` and `python3` are all
already used by the suite.
