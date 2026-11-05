# Read the agent dir pi reads, however PI_CODING_AGENT_DIR is written

## Summary

In the operator container, `agent_pi.check_stored_login` reads `auth.json` and
`models.json` from the dir `agent_pi._pi_auth_json` works out from
`PI_CODING_AGENT_DIR`. Today that uses `os.path.expanduser` and a `Path` that
leaves `..` to the kernel, so it doesn't always reach the dir pi 1.4.0 reads.
This change ports pi's own resolution: `getAgentDir` (`dist/config.js`),
`normalizePath` (`dist/utils/paths.js`), Node's `os.homedir()`, Node's
`path.posix.join`, and a strict subset of `fileURLToPath`. A value the port
can't match exactly stops the run. A value that comes out relative stops it with
today's message.

Files to change:

- `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`
- `.claude/skills/sssf/tests/test_agent_pi.py`
- `.claude/skills/sssf/references/config.md`

## What pi 1.4.0 does (read from the installed package, and run)

I read `dist/config.js` and `dist/utils/paths.js` in pi 1.4.0, the version
installed here, and ran pi's own `getAgentDir()` under node v24.11.1 with
`HOME=/h`:

- `getAgentDir()`: `envDir = process.env.PI_CODING_AGENT_DIR`. If it's truthy,
  the result is `normalizePath(envDir)` with default options. Otherwise it's
  `join(homedir(), ".pi", "agent")`. An empty value counts as unset.
- `normalizePath` with default options on Linux: no trim, no unicode-space
  change, no `@` strip, and no win32 branch. With `expandTilde`, which defaults
  to true, and `home = os.homedir()`:
  - `"~"` returns `home`, unjoined.
  - A value starting `"~/"` returns `join(home, value[2:])`. (`~\` counts only
    on win32.)
  - A value matching `/^file:\/\//` (case-sensitive) returns
    `fileURLToPath(value)`.
  - Anything else is returned as written.
- Callers build each file as `join(getAgentDir(), "auth.json")`
  (`dist/core/auth-storage.js`), and `models.json` the same way.
- `os.homedir()` comes from libuv `uv_os_homedir`. It returns `$HOME` whenever
  `HOME` is set, even when it's empty (`HOME= node -e ...` prints `""`).
  Otherwise it returns the passwd entry's `pw_dir` for the effective uid. Python's
  `expanduser("~")` gives `/` for an empty `HOME`, so it can't be used.
- Node's posix `join`: drop empty args, join the rest with `/` (`"."` if none
  are left), then `normalize`. `normalize` folds `.`, `..` and repeated `/` as
  text. On an absolute path, `..` at the root stays at the root. On a relative
  path, a `..` with nothing left to pop is kept. Python's `posixpath.normpath`
  keeps a leading `//`, which Node's doesn't, so don't use it.
- Node decodes environment variables as UTF-8 and turns invalid bytes into
  U+FFFD. With `PI_CODING_AGENT_DIR=$'/tmp/\xff'`, pi reads `/tmp/\uFFFD`.
  Python's `os.environ` keeps the raw byte as a surrogate, so the two name
  different files.
- `fileURLToPath` parses the value as a WHATWG URL first. That folds dot segments
  per URL segment, empty segments included, decodes `%XX`, drops `?query` and
  `#fragment`, maps `localhost` to an empty host, throws on any other host, and
  has a Windows drive-letter quirk (`file:///C:/../x` gives `/C:/x`).

Reference table. Each value is pi's real output, `join(getAgentDir(),'auth.json')`,
with `HOME=/h`:

| `PI_CODING_AGENT_DIR` | pi reads |
|---|---|
| unset | `/h/.pi/agent/auth.json` |
| `""` | `/h/.pi/agent/auth.json` |
| `~` | `/h/auth.json` |
| `~/` | `/h/auth.json` |
| `~/y` | `/h/y/auth.json` |
| `~/a/../b` | `/h/b/auth.json` |
| `~root/x` | `~root/x/auth.json` (relative) |
| `/a/link/../b` | `/a/b/auth.json` (whatever `link` is) |
| `/a/./b//c/` | `/a/b/c/auth.json` |
| `/..` | `/auth.json` |
| `file:///a/b` | `/a/b/auth.json` |
| `file:///a/b/../c` | `/a/c/auth.json` |
| `file:///` | `/auth.json` |
| `file:////a` | `/a/auth.json` |
| `file:///a//../b` | `/a/b/auth.json` (a plain `join` would give `/b`) |
| `file://localhost/x` | `/x/auth.json` |
| `file://host/x` | throws `ERR_INVALID_FILE_URL_HOST` |
| `file:///a%20b` | `/a b/auth.json` |
| `file:///a?q#f` | `/a/auth.json` |
| `file:/a` | `file:/a/auth.json` (relative) |
| `FILE:///a` | `FILE:/a/auth.json` (relative) |
| `~` with `HOME=""` | `auth.json` (relative) |
| `/tmp/<0xff byte>` | `/tmp/\uFFFD` |

## Threat model

The agent runs as the engineer's own user in the operator container, so it can
write anything that user can: files under `$HOME`, `~/.pi/agent/`, symlinks
anywhere it can write, and the worktree. It can't set the operator's
environment. `PI_CODING_AGENT_DIR` and `HOME` come from the engineer's shell, or
from `.env`, which `utils.py` loads at import before anything reads them. This
guard makes sure that the `auth.json` and `models.json` SSSF checks before a pi
agent starts in the container are the same files pi will open. Pi uses an openai
credential found there ahead of the broker's token.

At this level the guard can't stop:

- A file swapped between the check and pi's read (a race; already a known limit).
- An agent editing what a later agent's pi reads, after the check for that
  call. The check runs before each call, so the next call catches it.
- Anything pi loads other than these two files: extensions, settings packages
  (already known limits).

## Baseline

`main` at `bcef944` ("Clear /tmp in the Containerfile's claude install step").
Every finding is judged against it. At the baseline:

- `_pi_auth_json()` (`agent_pi.py:269`) returns
  `Path(os.path.expanduser(PI_CODING_AGENT_DIR)) / "auth.json"`, or
  `Path.home() / ".pi/agent/auth.json"`. It's evaluated once, at import, into
  `PI_AUTH_JSON`.
- `check_stored_login(path=None)` uses `path or PI_AUTH_JSON`, stops on a path
  that isn't absolute (`PI_CODING_AGENT_DIR puts pi's auth.json at {path}, a
  relative path, ...`), and reads `path.parent / "models.json"`.
- Every `file://` value stops as relative, with a message naming the wrong path
  (`file:/...`). `~user/x` is expanded to user's home, which pi doesn't do. A
  symlink before `..` makes SSSF read through the link while pi reads the
  text-folded path.

## Bar

The change must:

- Make `check_stored_login()` with no argument read
  `join(getAgentDir(), "auth.json")` and `join(getAgentDir(), "models.json")`
  exactly as pi 1.4.0 computes them, for every value of `PI_CODING_AGENT_DIR`
  and `HOME` that the port accepts.
- Stop the run, before any pi agent starts, for a value it can't match exactly:
  - a `file://` URL outside the strict subset below
  - a value, or the `HOME` that's used, that isn't valid UTF-8
  - no `HOME` and no passwd entry
- Stop with today's relative-path message, naming the path pi would read,
  whenever that path is relative. That includes `~user/x`, `file:/a`,
  `FILE:///a`, and `~` with `HOME=""`.
- Keep an unset or empty variable reading `~/.pi/agent/auth.json`, built as pi
  builds it. For any `HOME` that's an absolute path with no `.`, `..` or `//`,
  that's byte-for-byte today's path.

The change must never:

- Change a host run, a VM run, or a Claude Code roster in the container.
  `check_stored_login` is still called only from `_in_container`, on a local
  tree with `SSSF_PI_TOKEN_SOCKET` set.
- Keep an agent file, run agent code, or delete or overwrite an engineer file.
  The check only stats and reads. It spawns no process: no `node`, no `pi`.
- Pass a value the baseline stops, unless the port shows pi reads exactly the
  file it checked. Every baseline stop for a `file://` value either becomes a
  correct check of the file pi reads or stays a stop.
- Print a file's contents. Messages name paths and the variable's value only.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Add `import pwd` to the imports.

Replace `_pi_auth_json` and the `PI_AUTH_JSON = _pi_auth_json()` line
(currently lines 269-280) with the following. Keep the names, because tests
and review harnesses use them.

```python
def _node_join(*parts: str) -> str:
    """Node's path.posix.join: drop empty parts, join with /, then fold ., ..
    and repeated / as text, the way normalize does. A .. at the root of an
    absolute path stays at the root; a relative path keeps a leading .. it
    can't pop. posixpath.normpath differs (it keeps a leading //)."""
    joined = "/".join(p for p in parts if p)
    if not joined:
        return "."
    absolute = joined.startswith("/")
    out: list[str] = []
    for seg in joined.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not absolute:
                out.append("..")
            continue
        out.append(seg)
    body = "/".join(out)
    return "/" + body if absolute else (body or ".")


# The file:// URLs whose fileURLToPath SSSF can match exactly: no host, no
# %-escape, ?, #, \, :, | or empty segment, only characters the URL parser
# leaves as they are. On these, URL dot-segment folding then join is the
# same as join alone.
_URL_SEGMENT = r"[A-Za-z0-9._~!$&'()*+,;=@-]+"
_PLAIN_FILE_URL = re.compile(rf"file://(/(?:{_URL_SEGMENT}(?:/{_URL_SEGMENT})*/?)?)")


def _utf8(value: str, name: str) -> str:
    """value, if Node would read the same bytes. Node decodes the environment as
    UTF-8 and turns a bad byte into U+FFFD, a different path."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise sandbox.SandboxFailed(
            f"{name} isn't valid UTF-8, so SSSF can't tell which dir pi reads its "
            "logins from; set it to a plain absolute path.") from None
    return value


def _node_homedir() -> str:
    """Node's os.homedir() (libuv uv_os_homedir): $HOME whenever HOME is set,
    even empty, else the passwd entry for the effective uid."""
    if "HOME" in os.environ:
        return _utf8(os.environ["HOME"], "HOME")
    try:
        return _utf8(pwd.getpwuid(os.geteuid()).pw_dir, "the passwd home dir")
    except KeyError:
        raise sandbox.SandboxFailed(
            "HOME isn't set and there's no passwd entry for this user, so SSSF can't "
            "tell where pi reads its logins; set HOME.") from None


def _pi_auth_json() -> Path:
    """Where pi reads its stored logins, worked out as pi 1.4.0 does:
    join(getAgentDir(), "auth.json"). getAgentDir (dist/config.js) is
    normalizePath(PI_CODING_AGENT_DIR) when that's set and non-empty
    (dist/utils/paths.js: "~" is the home dir, "~/rest" is join(home, rest), a
    file:// URL goes through fileURLToPath, anything else is as written; ~user
    is not expanded), else join(homedir(), ".pi", "agent"). Any value SSSF
    can't match exactly raises SandboxFailed. A relative result is returned as
    is, for check_stored_login to stop on. No other override: the check must
    read what pi reads."""
    agent_dir = os.environ.get("PI_CODING_AGENT_DIR")
    if not agent_dir:
        return Path(_node_join(_node_homedir(), ".pi", "agent", "auth.json"))
    agent_dir = _utf8(agent_dir, "PI_CODING_AGENT_DIR")
    if agent_dir == "~":
        return Path(_node_join(_node_homedir(), "auth.json"))
    if agent_dir.startswith("~/"):
        return Path(_node_join(_node_homedir(), agent_dir[2:], "auth.json"))
    if agent_dir.startswith("file://"):
        url = _PLAIN_FILE_URL.fullmatch(agent_dir)
        if not url:
            raise sandbox.SandboxFailed(
                f"PI_CODING_AGENT_DIR is a file:// URL ({agent_dir!r}) that SSSF can't turn "
                "into a path exactly as pi does; write it as a plain absolute path.")
        return Path(_node_join(url.group(1), "auth.json"))
    return Path(_node_join(agent_dir, "auth.json"))


PI_AUTH_JSON: Optional[Path] = None   # tests monkeypatch this; None works it out per call
```

Builder's notes on this code:

- `Path(...)` of an already folded string adds no folding of its own. Its `.`
  and `//` collapsing has nothing left to do, so `str(Path(x)) == x` for every
  result above except `"."`, which never occurs because a file name is always
  joined last.
- `_PLAIN_FILE_URL` must use `fullmatch`, so `\n` and other trailing text can't
  slip past `$`. It allows `.` and `..` segments, which fold the same way in a
  URL and in `join` once there are no empty segments. It rejects
  `file://localhost/x`, `file:///a%20b`, `file:///a?q`, `file:////a`,
  `file:///a//../b`, `file:///C:/x`, `file://` with nothing after it, and
  `file://host/x`. All of those stop the run, and all of them stop at the
  baseline too, as relative paths.
- In `"file://"` + `"/"`, `url.group(1)` is `"/"`, so the result is
  `/auth.json`, which matches pi.

Change `check_stored_login`:

- `path = path or PI_AUTH_JSON` becomes `path = path or PI_AUTH_JSON or _pi_auth_json()`.
  The environment is now read per call, as `child_env` already does. A
  `SandboxFailed` from `_pi_auth_json` propagates, so the run stops before pi
  starts.
- Keep the relative-path stop and its message exactly as they are.
- `models_path = path.parent / "models.json"` stays. The path is folded text
  ending in `auth.json`, so its parent is pi's `getAgentDir()` folded, and
  `parent / "models.json"` equals `join(getAgentDir(), "models.json")`.
- Add a line to the docstring: the dir is pi's, worked out by `_pi_auth_json`.

Nothing else in the module changes. `_in_container` keeps calling
`check_stored_login()` with no argument.

### 2. `.claude/skills/sssf/tests/test_agent_pi.py`

Update `test_the_auth_path_follows_pi_and_ignores_PI_AUTH_PATH`. Its three
assertions still hold. Keep them.

Add these tests. Each sets `HOME` with `monkeypatch.setenv` and
`PI_CODING_AGENT_DIR` with `setenv`, or `delenv(raising=False)`. Expected
values come from the reference table above, which is pi 1.4.0's real output.
Say so in a comment naming pi 1.4.0 and `getAgentDir`.

1. `test_the_agent_dir_is_worked_out_as_pi_does`: a parametrized table, with
   `HOME` set to `str(tmp_path)` and values written relative to it (`/h` in the
   table stands for `tmp_path`). Assert `str(agent_pi._pi_auth_json())` equals
   the expected string. Cases:
   - unset and `""` give `<h>/.pi/agent/auth.json`
   - `~` and `~/` give `<h>/auth.json`
   - `~/y` gives `<h>/y/auth.json`
   - `~/a/../b` gives `<h>/b/auth.json`
   - `/a/./b//c/` gives `/a/b/c/auth.json`
   - `/..` gives `/auth.json`
   - `file:///a/b` gives `/a/b/auth.json`
   - `file:///a/b/../c` gives `/a/c/auth.json`
   - `file:///` gives `/auth.json`
   - a `HOME` ending in `/` with `~/y` gives no doubled slash
2. `test_a_relative_agent_dir_stops_with_todays_message` (parametrized):
   `~root/x`, `~nosuchuser/x`, `rel/x`, `file:/a`, `FILE:///a`, `./x`. Also
   `HOME=""` with `~`, and with the variable unset (pi gives `.pi/agent/auth.json`).
   Call `agent_pi.check_stored_login()` with `PI_AUTH_JSON` left `None`. Assert
   `SandboxFailed` matching `"a relative path"` and naming pi's relative path
   (`~root/x/auth.json`, `file:/a/auth.json`, `auth.json` and so on). Use
   `monkeypatch.chdir(tmp_path)` and create `tmp_path/"~root/x/auth.json"` with
   an `openai` entry, to show the stop comes before any read. Keep
   `test_a_relative_agent_dir_stops` as it is.
3. `test_a_file_url_sssf_cant_match_stops` (parametrized):
   `file://localhost/x`, `file://host/x`, `file:///a%20b`, `file:///a?q#f`,
   `file:////a`, `file:///a//../b`, `file:///C:/../x`, `file://`,
   `file:///a\nb`, `file:///a b`. Call `check_stored_login()` and assert a
   `SandboxFailed` matching `"file:// URL"`.
4. `test_a_symlink_before_dotdot_is_read_as_pi_reads_it`: under `tmp_path`, make
   `real/b/` holding an `auth.json` with an `openai` entry, `other/b/` holding a
   clean `auth.json`, and `link` pointing at `other/sub` (make `other/sub/`).
   Set `PI_CODING_AGENT_DIR=<tmp>/real/link/../b`, where `real/link` is a
   symlink to `<tmp>/other/sub`. The kernel resolves `real/link/../b` to
   `other/b`, which is clean. Pi folds it as text to `real/b`, which has the
   login. Assert `check_stored_login()` stops, naming `<tmp>/real/b/auth.json`.
   Run it a second time with the roles swapped: login under `other/b`, clean
   `real/b`. It passes. Repeat the first half for `models.json` with
   `providers.openai.apiKey`, and assert the message names `<tmp>/real/b/models.json`.
5. `test_a_file_url_agent_dir_is_checked`: `PI_CODING_AGENT_DIR=file://<tmp>/d`,
   with `<tmp>/d/auth.json` holding an `openai` entry. Assert the stop names
   `<tmp>/d/auth.json`, not `file:/...`. Build the URL only when `str(tmp_path)`
   matches `_URL_SEGMENT`'s character set. pytest's tmp paths do, so assert that
   precondition rather than skipping.
6. `test_bad_utf8_stops` (parametrized over `PI_CODING_AGENT_DIR` and `HOME`):
   set the variable through `os.environ` to `"/tmp/\udcff"`, the surrogate
   Python uses for byte `0xff`. Use `monkeypatch.setenv`, which accepts it on
   POSIX. If it doesn't, use `monkeypatch.setitem(os.environ, ...)`. For `HOME`,
   leave `PI_CODING_AGENT_DIR` unset. Assert a `SandboxFailed` matching `"UTF-8"`.
7. `test_no_home_uses_the_passwd_entry`: `delenv("HOME")` and
   `PI_CODING_AGENT_DIR="~/z"`. Assert `_pi_auth_json()` is
   `Path(pwd.getpwuid(os.geteuid()).pw_dir) / "z/auth.json"`, folded the same way.
   Then monkeypatch `agent_pi.pwd.getpwuid` to raise `KeyError`, and assert a
   `SandboxFailed` matching `"HOME"`.
8. `test_the_agent_dir_is_read_per_call`: with `PI_AUTH_JSON` left `None`, set
   the variable to dir A (clean), and call: it passes. Set it to dir B (holding
   an `openai` login), and call: it stops naming B.
9. `test_a_host_run_never_works_out_the_agent_dir`: set
   `PI_CODING_AGENT_DIR="file://host/x"`, which would stop the run in the
   container, and `delenv("SSSF_PI_TOKEN_SOCKET")`. Assert
   `_in_container({"A": "1"}, sandbox.LOCAL) == {"A": "1"}`. With the socket set
   and a non-local tree (`object()`), assert it's unchanged too. That covers the
   VM path.

Existing tests that monkeypatch `agent_pi.PI_AUTH_JSON` to a `tmp_path` file
(the `broker` fixture, `test_a_missing_socket_stops`, and the model-check tests)
keep working, because a set `PI_AUTH_JSON` wins over `_pi_auth_json()`. The
existing Claude Code roster tests (`test_a_claude_code_roster_ignores_models_json`,
`test_a_claude_code_roster_never_asks_the_broker`) must pass unchanged.

These are unit tests of `agent_pi`. They build no `sssf.config.yaml`. Any test
here that needs a config must load `.claude/skills/sssf/templates/sssf.config.yaml`,
as the existing ones do, never a test-only one.

### 3. `.claude/skills/sssf/references/config.md`

At line 434, replace `So does anything at that path that isn't a regular file,
or a relative `PI_CODING_AGENT_DIR`.` with wording along these lines:

> SSSF finds that dir as pi 1.4.0 does: `~` or a leading `~/` is the home dir,
> a `file://` URL is read as a path, and `.` and `..` fold as text, so a
> symlink before a `..` doesn't move it. Any other value is used as written,
> `~user/x` included. So does anything at that path that isn't a regular file,
> a `PI_CODING_AGENT_DIR` that comes out relative (such as `~user/x`), a
> `file://` URL with a host, a `%`-escape, a query, an empty segment or any
> character beyond letters, digits and `._~!$&'()*+,;=@-`, and a value or
> `HOME` that isn't UTF-8.

Keep the rest of the paragraph. Add no new **Known limit** entry: each case the
port can't match stops the run, so nothing is left unchecked. Check the
existing limits at lines 436-460 still read true. None of them claims the path
is resolved with `expanduser`, and none needs to change.

### Docs this change makes out of date

- `.claude/skills/sssf/references/config.md` line 434, covered above.
- The docstrings of `_pi_auth_json` and `check_stored_login`, covered above.
- The test module's docstring needs no change.
- No CHANGELOG is kept for the skill.
- No README or help text names `PI_CODING_AGENT_DIR`. The builder confirms with
  `grep -rn PI_CODING_AGENT_DIR .claude/skills/sssf --include=*.md`, and updates
  any other hit outside `app_docs/`.
- For the documenter: any `app_docs/` write-up of the 3ca0a481 or 5e778abf
  changes that says the check expands `~` with `expanduser`, or follows
  `PI_CODING_AGENT_DIR` "with `~` expanded", now describes old behaviour. Note
  that; don't edit it here.

## Out of scope

These holes stay open, and a reviewer should file them as old:

- The check and pi read the files at different moments, so a swap in between
  passes (known limit, line 448).
- Credentials pi takes from anywhere other than `auth.json` and `models.json` in
  the agent dir: extensions, settings packages, other stored logins, and
  `baseUrl`/`api`/`oauth` (known limits).
- `check_stored_login` reads `os.environ`. `child_env` hands pi
  `operator_env()`, a copy of it, which doesn't touch `HOME` or
  `PI_CODING_AGENT_DIR`. A future change to `operator_env` that rewrote either
  would split them again. Nothing does today.
- pi versions other than 1.4.0. The port follows 1.4.0's `getAgentDir` and
  `normalizePath`, and a later pi that resolves the variable differently needs a
  new port.
- `file://` URLs outside the strict subset, which stop the run rather than being
  checked. That's the bar's choice, and the baseline stopped them all.
- A relative result is never checked against the worktree's cwd. It stops, as
  today.

## Verification

1. Run the skill's agent_pi tests, which must pass:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py`
2. Run the whole skill suite, which must pass as at the baseline:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests`
3. Optional: compare the port against pi itself, if `pi` 1.4.0 is installed (it
   is on this machine). For a handful of table values, run pi's own
   `getAgentDir()` with
   `node --input-type=module -e "import {getAgentDir} from '<pi pkg>/dist/config.js'; import {join} from 'node:path'; console.log(join(getAgentDir(),'auth.json'))"`
   and check it matches `agent_pi._pi_auth_json()` under the same `HOME` and
   `PI_CODING_AGENT_DIR`. Here `<pi pkg>` is the `@earendil-works/pi-coding-agent`
   package dir under the active node's global `node_modules`. Don't add this as a
   test: the suite must not depend on node or pi.
4. Confirm no host, VM or Claude Code path changed:
   `git diff bcef944 -- .claude/skills/sssf/templates/adws/adw_modules/` should
   touch only `agent_pi.py`. Inside it, only `_pi_auth_json`, its new helpers,
   the `PI_AUTH_JSON` line, the `pwd` import and `check_stored_login` change.

## For the engineer

- `adws/adw_modules/agent_pi.py` is this repo's installed copy of the template
  module. It's protected, and identical to the template at `bcef944`. After this
  change merges, update SSSF from the skill, as in `0e61b73` ("Update SSSF to
  skill 577801e"), so `adws/adw_modules/agent_pi.py` and
  `adws/adw_sssf_config/stamp.json` pick up the new check. The builder must not
  edit either.
