# Move big inputs into an OpenShell VM by upload; judge the key by what reaches the VM

## Summary

Three changes to the remote-sandbox guard code, plus docs.

1. OpenShell's adapter (`_VM.run_process`) sends an input over 1 MiB by `openshell sandbox upload`, then runs the same command with its stdin read from the uploaded file. Smaller inputs stay on the pipe, byte for byte as on the baseline.
2. The API-key refusal judges the env a VM command would get. One check inside `Remote._env` covers every `run`, `commit` and `start`, and `open()` runs it before any VM exists.
3. A Claude Code agent started in an OpenShell VM gets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`.

All paths below are from the repo root.

## Earlier plans

This is the third plan of session 459e0656.

- `specs/459e0656_openshell-upload-inputs.md` was sent back by the spec review (`spec_review_1.md` in the handoff folder): its key check missed `run(env=...)`, which `quality.py` (line 83) and `worktree.py` (line 310) call with their own env.
- `specs/459e0656_openshell-upload-inputs_v2.md` moved the check into `Remote._env`, made `credentials()` judge both key names by default, and dropped a refusal for a big env frame.
- This plan is the second one's design, judged against the new baseline. Nothing in the design changed.

## Baseline

`main` at `40abfcf`. Every finding is judged against that commit.

The earlier plans were judged against `5e4be81`. Between the two, `main` changed three files: `.claude/skills/sssf/cookbooks/install.md`, `.claude/skills/sssf/cookbooks/run_adw.md` (both gained an OpenShell image check) and `.claude/skills/sssf/tests/test_rollback_no_programs.py`. This plan touches none of them, and every file it does touch is the same at both commits. The planner's worktree was still at `5e4be81` when this was written, so the line numbers below were read there; they hold at `40abfcf`.

At the baseline:

- `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py` hands the VM bytes on stdin in five places, all through `run_process(..., input=...)`: `_clone` (the git bundle, line 583), `_open_relay` (the relay script, 748), `write_text` (837), `install` (851), `handoff_in` (the gzipped tar, 860). Nothing else passes `input`.
- `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`'s `_VM.run_process` joins the env frame and the input and pipes both to `openshell sandbox exec`. `create_process` pipes the frame only.
- `credentials()` raises when `os.environ` holds `ANTHROPIC_API_KEY`, whether or not it would reach the VM.
- `Remote._env` passes only `SSSF_` and `OBS_` names, less `OBS_AUTH_TOKEN`. `_run` and `start` are its only callers.

## What was found

### OpenShell 0.1.7's limits

This session could not read OpenShell's source: the network is closed to it (HTTPS returns 403 at the proxy) and the CLI's install folder is unreadable (permission denied). Both look deliberate, so neither was worked around. What follows is from the CLI's own text.

- **stdin: 4 MiB.** Cited from the CLI's error in the 2026-11-18 run: "piped stdin exceeds the 4 MiB limit; use `sandbox upload` for larger input". Whether the count includes anything but the raw piped bytes is not known.
- **stdout: no limit found, not measured.** `openshell sandbox exec --help` (0.1.7) says "Output is streamed to the terminal in real-time" and names no limit. That is not proof there is none. The way out (`handoff_out`, `commits_out`, the saved patch, `read_text`) therefore stays as it is, and the measurement is under "For the engineer".
- **upload.** `openshell sandbox upload --help` (0.1.7): `upload [--no-git-ignore] <NAME> <LOCAL_PATH> [DEST]`. "Inside a Git work tree, `.gitignore` rules are applied by default. Outside a Git work tree, uploads proceed unfiltered with a warning. Filtering errors or empty selections stop the upload; use `--no-git-ignore` to intentionally upload everything." And for `DEST`: "For an unfiltered single-file upload, an existing directory or a trailing slash places the file inside that directory. A missing nested path without a trailing slash renames the file." The help names no size limit. Who owns an uploaded file in the VM is not documented.

### Vercel and E2B

Nothing shows the same limit there, so they are unchanged. E2B's adapter already writes an input with its files API, not stdin (`.claude/skills/sssf/templates/adws/adw_modules/sandbox_e2b.py`, `_VM.run_process`). Vercel passes `input` to its SDK and no failure is recorded. The upload sits wholly in OpenShell's adapter, so `sandbox_remote.py`'s commands are the same for all three.

### How a host variable reaches a VM

Every way, across the three providers, and where the key check judges it after this change:

| Way | Where | What crosses | Judged |
|---|---|---|---|
| `Remote._env` | every `run`, `commit` and `start`, with the caller's `env` or `os.environ` | names starting `SSSF_` or `OBS_`, less `OBS_AUTH_TOKEN`; `CLAUDE_CODE_SHELL_PREFIX` when `SSSF_REFUSE_RULES` crosses; `SSSF_PI_TOKEN` renamed `OPENAI_API_KEY`; `UV_PYTHON`, computed | in `_env` itself, on the dict it returns, at every call; and at `open()` on `os.environ`, before any VM is made |
| `Remote.start`'s additions | agents only | `pi_env` and `cc_env` (fixed per provider), `CLAUDE_CODE_OAUTH_TOKEN` from the token file, `OBS_SERVER_URL` (the relay's own URL) | `pi_env` and `cc_env` at `open()`. The other two are set under their own names, which are not key names |
| `_create` | VM creation | none. Vercel passes tags and a time limit. E2B passes a template, a timeout and metadata. OpenShell passes `--no-auto-providers` and no `--env`, so the gateway attaches no host credential | nothing to judge |
| the adapters' own calls | `run_process`, `create_process` | only the `env` dict above: Vercel and E2B pass it to the SDK, OpenShell frames it on stdin | as row one |
| SSSF's raw commands | `_must`, `_call`, the relay's `create_process` | no env at all | nothing to judge |
| files | clone, handoff, `install`, `write_text` | committed files, the handoff folder and SSSF's own files. `.env` is untracked and never enters | not a variable |

So every host value that reaches a VM as a variable passes through `_env`, whoever the caller is: `quality.py` and `worktree.py` call `run(..., env=...)` with their own env, and `agent_cc` and `agent_pi` call `start(..., env=...)`. One check there covers all of them. No row passes a name Claude Code reads as an API key today, which is why a host `ANTHROPIC_API_KEY` never reaches a VM.

The names Claude Code reads as an API key, as SSSF already lists them in `agent_cc.API_KEY_VARS`: `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`.

### The telemetry setting

`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is in Claude Code's environment-variable reference (`https://code.claude.com/docs/en/env-vars`) and its data-usage page (`https://code.claude.com/docs/en/data-usage`). As this planner recalls the text, it is the equivalent of setting `DISABLE_AUTOUPDATER`, `DISABLE_FEEDBACK_COMMAND` (once `DISABLE_BUG_COMMAND`), `DISABLE_ERROR_REPORTING` and `DISABLE_TELEMETRY`. **The page was not opened in this session, because the network is closed, so the citation is from memory.** The engineer confirms it (see "For the engineer").

It is a narrowing under OpenShell whatever the exact list, for this reason: the VM's policy lets `claude.exe` reach `api.anthropic.com:443` and the read-only package registries, and nothing else. The setting only turns traffic off. What an SSSF run needs from Claude Code in the VM:

- the model call, to `api.anthropic.com`: the setting's name and documentation leave essential traffic alone;
- the sign-in: `CLAUDE_CODE_OAUTH_TOKEN` in the env, no network step;
- the version check SSSF relies on: `claude --version` in `_check_tools`, a local command.

The auto-updater it turns off could not write anyway, since the image's Claude Code is under `/usr`, which the policy mounts read-only. So change 3 ships, for OpenShell only, in one class attribute that is easy to take out. Whether the Datadog rows stop is not provable without a real VM; the engineer's first run shows it.

## Threat model

Under `--sandbox openshell` the agents run inside the VM as `ubuntu`. There an agent owns every file it can reach: the clone, the handoff folder, all of `/tmp`. It can leave processes running between calls. It cannot read the host's files or env. It reaches the host only through what SSSF brings back.

On the host, SSSF runs as the engineer's own user. So does any agent of another run under `sandbox: none`, and anything else the engineer runs. Such a process can write the host's `/tmp` and the repo's data folder, where the bundle is made.

What `upload` adds is a host command that reads a host path and writes a VM path. The risks:

- **A host path an agent chose or linked.** The upload would carry host bytes the piped write never sent, such as `.env`, into the VM.
- **The `.gitignore` filter.** Inside a work tree it can drop the file or stop the upload on an empty selection. Anyone can make `/tmp` a work tree by creating `/tmp/.git`.
- **Where the file lands.** A name the VM could guess could be planted first.

For the key: an agent's own env, and a quality check's, are built on the host from `.env`, the shell and `agent_env`. Only `_env` decides what of that crosses, so only a change to `_env`'s rules, or to a provider's fixed dicts, could carry a key across. The check guards those.

What this guard cannot stop at its level:

- A VM process can change the uploaded file between the upload and the command that reads it. It could already change the destination right after a piped write, so the VM gains nothing.
- A host process running as the engineer's user can race a write into the staging folder. Mode 0700 stops other users only.
- What OpenShell's `upload` does inside the gateway.
- An agent in the VM can start its own `claude` with any env. The setting covers the Claude Code that SSSF starts. The network policy still denies the rest, and still logs it.

## Bar

Against `40abfcf` the change must:

- Open a VM and clone a branch whose bundle is 21 MB, through the fake CLI taught the 4 MiB refusal. Put a handoff folder over 4 MiB in, and bring it back.
- Fail in that fake, with OpenShell's message, when the adapter is forced back onto the pipe.
- Send exactly the bytes the piped write sent. The file lands where the piped write put it, owned the same way, kept or removed as on the baseline, because the same command writes it.
- Open a sandbox with `ANTHROPIC_API_KEY` in the host's `os.environ` and not in the VM's env.
- Refuse, as today, when `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` is in what a VM command would get: at `open()` before any VM is made, and at any later `run`, `commit` or `start` whose env would carry one, before the command is sent.
- Give a Claude Code agent in an OpenShell VM `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, whatever the host's value. A pi agent doesn't get it.

It must never:

- Pass `upload` a path under the run's tree, its session folder or the data folder, or any path SSSF didn't create in the same call.
- Let a file planted beside the bundle, or a symlink in its place, reach the upload command or the VM.
- Put an env value or the token in the uploaded file or on a command line. The env frame stays on stdin.
- Change any command a Vercel or E2B run sends.
- Change an OpenShell run's commands, trace events or saved patch when every input is 1 MiB or less.
- Stop a run that works on `main`, other than one whose VM env would hold a key.
- Break an existing test. None is edited or deleted.
- Keep an agent's file, run an agent's code, or delete or overwrite an engineer's file, where the baseline does otherwise. The staging folder is SSSF's own and is removed in the same call.

## The change

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

Add `import tempfile`, and three constants beside `KILL_SLACK`:

- `STDIN_LIMIT = 4 * 1024 * 1024`: OpenShell 0.1.7's limit, with its message quoted in the comment. It documents the number; the code compares against `STDIN_MAX`.
- `STDIN_MAX = 1024 * 1024`: the most SSSF pipes to one exec when there is an input, frame and input together. The margin is a quarter of the limit. It leaves 3 MiB for whatever OpenShell counts beyond the raw bytes; base64, the costliest likely encoding, adds a third.
- `UPLOAD_SECONDS = 120`: the upload's own timeout. It fits inside the 180 seconds `Remote._call` gives a `_must`.

Add `_VM._upload(self, body: bytes) -> str`:

1. `dest = f"/tmp/sssf-in-{uuid.uuid4().hex}"`. SSSF picks the name and the VM can't guess it. The path is missing, so by the help text the single file is renamed to it.
2. `staging = Path(tempfile.mkdtemp(prefix="sssf-up-"))`: fresh, mode 0700, in the system temp folder, where `handoff_out` already stages at the baseline.
3. Write `staging / "input"` with `os.open(..., O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)`, then the bytes.
4. Run `[CLI, "sandbox", "upload", "--no-git-ignore", self.name, str(staged), dest]` with `stdin=subprocess.DEVNULL`, `capture_output=True`, `text=True`, `timeout=UPLOAD_SECONDS`.
5. In `finally`, `shutil.rmtree(staging, ignore_errors=True)`.
6. `OSError`, `TimeoutExpired` or a non-zero exit raises `OpenShellError("openshell sandbox upload failed: ...")` with `_last(stderr)`, as `destroy` does.

Why `--no-git-ignore` is needed: the property wanted is that the selection is exactly the one file SSSF wrote. Without the flag that depends on git state above the staging folder, which SSSF can't pin: a `.git` in `/tmp`, or a `TMPDIR` inside a repo, would filter the file out and stop the upload, or change what `DEST` means, since the rename rule is given for an unfiltered upload. With the flag, "uploads everything" means the one file in a folder SSSF made a moment ago. For the same reason the code doesn't test whether the staging folder is inside a work tree: the answer could change before the upload runs, and refusing on it would stop runs for no gain. The staging folder is outside the run's tree and data folder whenever the system temp folder is; the tests check that.

Change `_VM.run_process`:

- Build `frame = _frame(env or {})`.
- No input: mode `null`, stdin is the frame. As today, whatever the frame's size.
- Input with `len(frame) + len(body) <= STDIN_MAX`: mode `input`, stdin is frame plus body. As today, byte for byte.
- Otherwise: `path = self._upload(body)`, stdin is the frame only, and the argv tail is `["file", path, command, *args]`.

There is no refusal of its own for a big frame. A frame up to 4 MiB still goes through, as on `main`, and past that OpenShell's own message says why.

Change `_SHELL` to take the new mode, keeping `null` and `input` as they are:

```sh
m=$1; shift
if [ "$m" = file ]; then f=$1; shift; exec 3<"$f"; rm -f "$f"; exec "$@" <&3 3<&-
elif [ "$m" = null ]; then exec "$@" </dev/null; else exec "$@"; fi
```

The file is opened, removed, then given to the command as stdin. So the upload leaves nothing in the VM even if the command is killed, and the command is still `exec`ed, which keeps exit codes and `--timeout` as they are. A failed open ends the shell with a non-zero code after the sentinel, which `_must` reports. A failed `rm` is ignored.

`create_process` doesn't change. Add `cc_env = {"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}` to `class OpenShell`, beside `pi_env`. Update the module docstring: env values still travel on stdin; an input over `STDIN_MAX` goes by `sandbox upload` from a fresh folder.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

No change to `_clone`, `write_text`, `install`, `handoff_in`, `_open_relay`, `handoff_out`, `commits_out` or `_run`.

- Add `KEY_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")` beside `HELD`, with a comment that these are the names Claude Code reads as an API key and that they match `agent_cc.API_KEY_VARS`.
- Add `_key_check(env) -> None`: raises `SandboxFailed` when `env` holds a non-empty value under a `KEY_NAMES` name. The message names the variable, says it would reach the VM and win over the subscription token there, and keeps the words "Unset it before a sandbox run".
- `credentials(keys, reaches=None)`: the missing-keys check is unchanged. Then `_key_check(os.environ if reaches is None else reaches)`, both names in either case. A caller that doesn't say what the VM gets is judged on the whole host env, which is the safe default and keeps `test_an_api_key_where_the_run_starts_stops_it` true. Only `open()` calls it in SSSF, and it always says.
- `Remote._env`: call `_key_check` on the dict it is about to return, just before the `return`. This is the one site that covers every `run`, `commit` and `start`, whatever env the caller passed.
- `Remote.open()`: its first line becomes `credentials(self.keys, {**self._env(None), **self.pi_env, **self.cc_env})`. `_env(None)` runs before `_create`, so the refusal still comes before any VM exists. The two fixed dicts are judged here once, since they never change in a run.
- `Remote`: add `cc_env: dict[str, str] = {}` beside `pi_env`, with a comment: what a provider adds to a Claude Code agent's env, fixed values only.
- `Remote.start()`: two edits, and no check of its own.
  - Move the line `agent_env = self._env(env)` up, to before `self._refresh(AGENT_SECONDS)`. `_env` reads only `env` and `self.python`, so nothing else changes, and a refusal now comes before the handoff is copied in or a relay is opened.
  - The branch that isn't a pi agent also does `agent_env.update(self.cc_env)`. It runs after `_env`, so no host value can win.
- Update the module docstring's `run` and `start` bullets for the key check.

`SandboxFailed` out of `run` or `start` is not new: `start` already raises it when `handoff_in` fails. With the shipped `PASSED` no key name can pass `_env`, so the check never fires in a run that works on `main`.

### 3. Tests

In `.claude/skills/sssf/tests/test_sandbox_openshell.py`:

Teach `FAKE`:

- On `sandbox exec` with more than `4 * 1024 * 1024` bytes of stdin: write ``error: piped stdin exceeds the 4 MiB limit; use `sandbox upload` for larger input`` to stderr and exit 1, before running anything.
- On `sandbox upload`: follow the help text. Without `--no-git-ignore` and with the local path inside a git work tree, fail with an empty-selection error. Copy the file to `DEST` (inside it when `DEST` is an existing folder or ends in `/`). Log, beside the argv: whether the local path is a symlink, its folder's listing, its size and SHA-256.
- A VM root, since the fake's VM is the host: for example a `FAKE_OPENSHELL_MAP` env of `from=to` pairs the fake applies to the exec's command words, its `--workdir` and the upload's `DEST`. `tests/test_sandbox_remote.py`'s fakes rewrite host paths under a `vm` folder the same way. Map the repo's folder and `/tmp/` under `tmp_path`, so the clone has an empty target and no 21 MB file lands in the real `/tmp`. Unset, the fake behaves as now, so existing tests are untouched. Set `OpenShell.owner` to the host's own user in these tests, since the fake runs `chown` for real.

Build each config from `.claude/skills/sssf/templates/sssf.config.yaml` with the file's own `shipped()` and `roster()` helpers. Build the big inputs in the test with `os.urandom`: a 22 MB file committed to the test repo, and a 5 MB file in the handoff folder. Share the big repo between tests with one fixture, to keep the run light. Nothing big goes on a command line.

New tests. Keep these words in the names, so the second command under "Verification" selects them:

- `test_big_bundle_clones_by_upload`: `open()` on the big repo succeeds; the VM's `/tmp/sssf-in.bundle` equals the bundle `git bundle create` makes; exactly one upload ran; the uploaded temp path is gone from the VM.
- `test_stdin_limit_fails_like_openshell`: with `osh.STDIN_MAX` patched above the bundle's size, the same `open()` raises `SandboxFailed` matching "piped stdin exceeds the 4 MiB limit".
- `test_big_handoff_goes_in_and_comes_back`: `handoff_in` puts the 5 MB file in the VM's handoff folder with the same bytes; a second 5 MB file made on the VM side comes back through `handoff_out`.
- `test_staged_upload_holds_only_the_input`: before `open()`, plant a file beside `sandbox-in.bundle` in the session folder, and in a second case a symlink at that name pointing to a decoy. Assert from the fake's log that the upload's local path is not under the repo, the session folder or the data folder, is a regular file and not a symlink, that its folder held that one file, that the flag `--no-git-ignore` was passed, that the staging folder is gone afterwards, and that neither the planted file's bytes nor the decoy's reached the VM's bundle. Assert nothing about the decoy itself (see "Out of scope").
- `test_small_input_stays_on_the_pipe`: an input under the limit makes no upload call, and its exec argv ends `"input", command, ...` as on the baseline.
- `test_nonessential_traffic_is_off_for_claude_only`: through `_started_env`, a Claude Code start holds `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC == "1"` with the host's `os.environ` and the call's `env` both saying `0`; a pi start doesn't hold the name. `Remote.cc_env` is empty, so Vercel and E2B add nothing.
- `test_key_reaches_only_when_the_vm_would_get_it`: with `ANTHROPIC_API_KEY` in `os.environ`, `open()` succeeds (`_create`, `_clone`, `handoff_in`, `_check_tools` patched as the all-pi test does, with a token file). With `sandbox_remote.PASSED` patched to add `"ANTHROPIC_"`, `open()` raises `SandboxFailed` for `ANTHROPIC_API_KEY`, and again for `ANTHROPIC_AUTH_TOKEN`, and `_create` was never called. With `OpenShell.cc_env` patched to hold a key, it raises too.

In `.claude/skills/sssf/tests/test_sandbox_remote.py`, add two tests and edit none:

- `test_key_reaches_run_env_refuses`, on the shared `box` fixture, so it runs for Vercel and E2B. With `sandbox_remote.PASSED` patched to add `"ANTHROPIC_"`: `box.run(["true"], env={"ANTHROPIC_API_KEY": "k"})` raises `SandboxFailed`, and so does the same call with `ANTHROPIC_AUTH_TOKEN`, and so does `box.start(["true"], env={"ANTHROPIC_API_KEY": "k"})`. In each case the fake VM got no command for the call. Without the patch, `box.run(["true"], env={**os.environ, "ANTHROPIC_API_KEY": "k"})` returns 0: the key is filtered out before the check, so a host key in a caller's env stops nothing.
- `test_key_reaches_credentials`: `credentials(keys, reaches={})` passes with the host key set; `credentials(keys, reaches={"ANTHROPIC_AUTH_TOKEN": "k"})` raises.

Mark a test `host_only` only if it can't pass under agentsh; none should need it.

### 4. Docs

- `docs/designs/openshell-sandbox.md`: decision 9 gets a dated sentence that the code piped the bundle until this change, and how files move now. "As built" gets a bullet for the 4 MiB stdin limit, the 1 MiB threshold, the staging folder, `--no-git-ignore` and why, the open-remove-exec shell line, and the fact that the way out is still stdout with its limit unmeasured. One more bullet each for the key check and the telemetry setting, with the citation marked unconfirmed until the engineer checks it.
- `.claude/skills/sssf/references/config.md`, the OpenShell section (about lines 727 to 731): name the 4 MiB stdin limit, say an input over 1 MiB goes by `openshell sandbox upload` and what those inputs are, and add the telemetry setting next to the `sandbox_denied` sentence. Say an API key on the host stops a sandbox run only when it would reach the VM.
- `docs/designs/remote-sandboxes.md`: the "As built" bullet at about line 126 ("The run stops before making a VM if `ANTHROPIC_API_KEY` is set") and decision 8's clause at about line 44 say the refusal applies only when the key would reach the VM, and that every VM command's env is judged.
- `docs/open.md`: one item, that `openshell sandbox exec`'s stdout limit and an uploaded file's owner are unmeasured, pointing at the engineer's check.
- Docstrings: both modules, as named above.
- The repo keeps no CHANGELOG, and no README usage changes.
- For the documenter: any earlier `app_docs/` write-up of the remote or OpenShell sandbox that says the bundle is piped, or that a host `ANTHROPIC_API_KEY` always stops a sandbox run, now describes old behaviour. Note it in the new write-up; the builder edits nothing under `app_docs/`.

## Verification

From `.claude/skills/sssf`, the touched files only, as the skill's own tests run:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_sandbox_remote.py -m 'not pushes and not host_only' -q
```

The new tests, by name. This exits non-zero until they exist:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_sandbox_remote.py -m 'not pushes and not host_only' -q -k 'big_bundle or stdin_limit or big_handoff or staged_upload or small_input or nonessential or key_reaches'
```

Neither is in the report's `checks`. In the planner's session the second one started pytest and stopped at collection, twice, with a `PermissionError` under `load_dotenv()` in `adw_modules/utils.py` (line 16), which searches upward for a `.env` the session may not read. That is this session's guard, not the plan's work, and it wasn't worked around; a check that can fail that way would fail every build.

Leave the whole suite to the verify step. No test starts a real VM. Check by reading the diff that `sandbox_remote.py`'s five `input=` lines, `handoff_out`, `commits_out` and `_run` are unchanged, that `start` has no key check of its own, and that `sandbox_vercel.py` and `sandbox_e2b.py` are untouched.

## Out of scope

Old holes this change leaves open, to be filed as old:

- `_clone` makes the bundle at `<session>/sandbox-in.bundle`, in the data folder. A host process that plants a symlink there before the run may get git to write through it. The upload never reads that path, but the baseline's write to it is unchanged.
- The stdout limit is unmeasured. A handoff, a commit bundle or a patch big enough to pass one would fail on the way out, as on the baseline.
- `/tmp/sssf-in.bundle` stays in the VM after the clone, as on the baseline.
- OpenShell's `_VM.run_process` ignores `sudo`, as on the baseline.
- An env frame over 4 MiB fails with OpenShell's message, as on the baseline.
- Vercel's own input limits, if it has any.
- Key names beyond the two: the cloud-provider settings (Bedrock, Vertex, Foundry) need more than one variable and none starts with `SSSF_` or `OBS_`.
- `SSSF_CLAUDE_AUTH=api_key` with a sandbox: the baseline refused the run whenever the key was set. Now it opens, and the VM still runs on the subscription token, since the key never crosses.
- Denial rows from a `claude` the agent starts itself, or from any traffic the setting doesn't cover.
- From the request: the sandbox switch procedure and the log-row names in the docs, the task-limit row, and the P3 host commit check rows.

## For the engineer

No protected path outside `.claude/skills/sssf/` needs a change. The repo's installed copy under `adws/` picks the change up by the usual skill update, which the builder must not do.

Checks only the engineer's session can run, since no agent or test starts a real VM. Record the results in `docs/designs/openshell-sandbox.md`, "The exec checks":

1. **Upload.** In a throwaway sandbox, upload a 21 MB file from a fresh `mktemp -d` folder with `--no-git-ignore` to a missing path such as `/tmp/sssf-in-check`. Confirm it lands as a file at exactly that path with the same SHA-256, and that the sandbox user can read and remove it (`stat -c '%U %a %s'`). If it isn't readable by `ubuntu`, the big-input path fails loudly at the clone and needs a follow-up.
2. **stdout.** `openshell sandbox exec -n <name> --no-tty -- head -c 67108864 /dev/zero | wc -c` should print 67108864. If it prints less or fails, the way out needs `download`, as a follow-up run.
3. **The telemetry citation.** Open `https://code.claude.com/docs/en/env-vars` and confirm what `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` turns off. If it lists something a run needs, remove `OpenShell.cc_env`.
4. **The first real run.** A Claude Code agent's call succeeds, and the trace holds no `sandbox_denied` row for `datadoghq.com`. If the rows remain, the setting doesn't cover them; say so in `docs/open.md`.
