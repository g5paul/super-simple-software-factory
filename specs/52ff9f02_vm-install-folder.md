# Plan: let SSSF's refuse files reach an E2B VM

`Remote.install` copies SSSF's own refuse file into the VM at its host path. On E2B the copy fails, because the VM's user can't make that file's folder. The fix: make the folder as root, the way `_clone` makes the clone's folders, and hand it to the VM's user. Then write the file as that user, as now.

All code is in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`, and all tests are in `.claude/skills/sssf/tests/test_sandbox_remote.py`. The hub's installed copy, `adws/adw_modules/sandbox_remote.py`, is protected. See "For the engineer".

## Baseline

The baseline is `main` at `0d127f8` ("Stamp the in-phase VM extension check into the hub"). Every finding is judged against that commit. At that commit:

- **`Remote.install(path)`** (`sandbox_remote.py:393`) does this:
  1. Computes `where = self._path(path)`.
  2. Returns if `where` is already in `self.installed`.
  3. Runs one command as the VM's user, with the host file's bytes as stdin: `sh -c 'mkdir -p "$(dirname "$0")" && cat > "$0" && chmod 755 "$0"' <where>`.
  4. Adds `where` to `self.installed`. A non-zero exit raises `SandboxFailed` from `_must`, and `where` is not recorded.
- **`Remote._clone`** (`sandbox_remote.py:288`) makes the run's tree, the handoff folder and the session folder as root with `sudo=True`: `sh -c 'mkdir -p "$1" "$2" "$3" && chown -R {self.owner} "$1" "$2" "$3"' sh <tree> <handoff> <session>`.
  - With `--worktree`, the tree is `<launch checkout>/adws/adw_data/worktrees/<adw_id>` (`worktree.path_for`). So `mkdir -p` leaves `<launch checkout>` and `<launch checkout>/adws` owned by root, and only the three leaf folders are handed to the user.
  - The VM's user therefore can't make `<launch checkout>/adws/adw_modules`. That is the E2B failure the request quotes.
- **Providers.**
  - `sandbox_vercel.Vercel.owner = "ubuntu"`. Vercel's SDK takes `sudo=True`.
  - `sandbox_e2b.E2B.owner = "user"`. Its `_VM.run_process` runs a `sudo=True` command as E2B's `root` user, and sends `input` through a temp file.
- **Callers.** Both install a constant path: SSSF's own module file beside them in the launch checkout.
  - `agent_cc._with_refusals` calls `tree.install(REFUSE_SHELL)`, `sssf_refuse.py`, before each Claude Code call in a repo with refuse rules.
  - `agent_pi._in_vm` calls `tree.install(REFUSE_EXTENSION)`, `sssf_rules.ts`, before each pi call in a VM with refuse rules.
  - Both calls come before the agent process starts, so a `SandboxFailed` from `install` stops the call before the agent runs.
- **Host install.** `sandbox.Local.install` (`sandbox.py:106`) only adds the exec bits on the host file. A host run never reaches `Remote`.
- **Tests.** `test_install_copies_a_host_file_into_the_vm_executable_once` covers the copy on both fakes. The fake VM is a temp folder owned by the test user, so `mkdir -p` as the "VM user" succeeds there. The fakes can't reproduce E2B's permission error, which is why the bug passed the suite.
  - `FakeVercel.calls` records `argv`, `env` and `cwd`, not `sudo`.
  - `FakeE2B.calls` records `user`.
  - `_no_chown` rewrites only `&& chown -R <user>` in root commands, so the fakes never run a real `chown`.

## Threat model

The agent runs as the VM's user in a VM, and as the engineer's own user on the host. In the VM it can:

- read and write anything the VM's user owns: the clone, the handoff folder, the session folder, and the installed refuse file, which it owns at the baseline on Vercel and will own on both providers after this change;
- run any command its tools allow, apart from what the refuse rules and the host's stream check catch.

This guard is the step that puts the refuse file where the agent's harness will run it, before each agent call. At its level, it can't stop:

- An agent in the VM overwriting or deleting the installed refuse file, or `chmod`ing it, between calls. `install` copies once per VM, so a later call runs whatever is there. A deleted or broken `sssf_refuse.py` fails every Claude Code `Bash` call, which fails closed. An edited one could pass a matching command. The host's stream check still reads the agent's stream and stops the run if a rule matches. The host has the same exposure: there, the agent's user owns the launch checkout's `adws/adw_modules/`.
- An agent placing files in the handed-over folder. That folder is outside the clone, and nothing SSSF loads reads it except the one installed file.

What it must keep doing:

- Fail closed. No agent call starts without the refuse file in place and executable.

## Bar

Against the baseline, the change must:

1. **Make the folder as root.** `Remote.install` makes the file's folder, the parent of `where`, with `sudo=True`. It hands that folder to `self.owner` with a non-recursive `chown`. On E2B, an agent call in a repo with refuse rules then gets the file instead of `Permission denied`.
2. **Write the file as the VM's user.** In a second command, not under `sudo`, it writes the host file's bytes to `where` and runs `chmod 755` on it, as today.
3. **Copy once per VM, at the host path, with the host file's bytes.** A second `install` of the same path runs no VM command at all.
4. **Fail closed.** A failure in either command raises `SandboxFailed`, leaves `where` out of `self.installed`, and so stops the agent call before its process starts, as today. A failure in the root command means no write is attempted.

The change must never:

- Change a host run. `sandbox.py` (`Local.install`), `agent_cc.py` and `agent_pi.py` are not edited.
- Change anything else the VM gets. `_clone`, `_env`, `handoff_in`, `start`, `run` and the tool check stay as they are.
- `chown` anything but the one folder: no `-R`, and no parent folder made by `mkdir -p`. Those stay root's.
- Write the file as root, or leave it with any mode other than `755`.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't. The only path written is SSSF's own file, at the path the baseline writes on Vercel, and it is written only in the VM.

## Out of scope

These are old holes or deliberate limits. File them as such.

- **The agent owns the installed refuse file.** It can rewrite it in the VM between calls, as it can at the baseline on Vercel, and as it can on the host. Making it root-owned and read-only would be a follow-up, and the request asks for it to be written as the VM's user.
- **Once per VM.** A file the agent changes after the first copy isn't copied again (baseline).
- **Root-owned parents.** `mkdir -p` as root leaves new parent folders, such as `<launch checkout>/adws`, owned by root, as `_clone` already does. Nothing in the VM needs to write there.
- **No real-VM test in the suite.** The fakes run as one local user, so they can't show E2B's permission error. The suite pins the commands' shape: root makes the folder, the user writes the file. The real proof is the manual E2B run under "For the engineer".
- **Decision 27's proof itself.** Re-running it is the engineer's, after the stamp.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

Replace the body of `Remote.install` after the `installed` early return with two `_must` calls:

```python
    def install(self, path) -> None:
        """Copy one of SSSF's own host files into the VM at the same path,
        executable, once per VM. The VM's clone holds the repo's committed
        files, which can lag the skill the host is running. Its folder is
        outside the clone, where the VM's user can't make folders, so root
        makes it and hands it over, as _clone does; the user writes the file."""
        where = self._path(path)
        if where in self.installed:
            return
        self._must("sh", ["-c", f'mkdir -p "$1" && chown {self.owner} "$1"', "sh", str(Path(where).parent)],
                   sudo=True)
        self._must("sh", ["-c", 'cat > "$0" && chmod 755 "$0"', where],
                   input=Path(path).read_bytes())
        self.installed.add(where)
```

Notes:

- Pass the folder as `$1` with `sh` as `$0`, as `_clone` does, so the path is never part of the shell text.
- The `chown` is not recursive. See the Bar.
- Keep every other line of the module as it is.

### 2. `.claude/skills/sssf/tests/test_sandbox_remote.py`

**Test helpers:**

- `_no_chown`: widen the pattern to `r"&& chown (-R )?\w+"`, so the fakes also skip the new non-recursive `chown`. `_clone`'s `-R` form is still stripped as before. Update its use comment if it needs one.
- `FakeVercel.run_process`: record `"sudo": sudo` in each `self.calls` entry. Keep the rest of the entry as it is.
- Add a small helper near the other helpers:

  ```python
  def _as_root(call: dict) -> bool:
      return bool(call.get("sudo")) or call.get("user") == "root"
  ```

  Also add `_words(call)`, returning `" ".join(call.get("argv") or [call.get("line", "")])`. The existing `test_the_vm_user_owns_the_runs_session_folder_for_pi_sessions` already uses that expression inline; it can stay as it is.

**Tests.** These run on both the `vercel` and `e2b` params through the `box` fixture. They need no config. If one is added that needs a roster, it must load `.claude/skills/sssf/templates/sssf.config.yaml`, never a test-only config.

- **(a) `test_install_makes_the_folder_as_root_and_writes_the_file_as_the_vm_user(box, tmp_path)`.**
  1. Make a host file at `tmp_path / "host" / "skill" / "adw_modules" / "sssf_refuse.py"`, with contents `"#!/bin/sh\necho refused\n"`.
  2. Record `before = len(box.raw.calls)`, call `box.install(script)`, and take `new = box.raw.calls[before:]`.
  3. Assert `len(new) == 2`.
  4. Assert `_as_root(new[0])`. Its words contain `mkdir -p` and `f"chown {box.owner}"`, do not contain `chown -R`, and contain the VM-mapped folder `box.fake._map(str(script.parent))` as a whole word. They do not contain `cat >`.
  5. Assert `not _as_root(new[1])`. Its words contain `cat >` and `chmod 755`, do not contain `mkdir`, and contain `box.fake._map(str(script))`.
  6. Assert the copied file has the host's bytes and is executable.
- **(b) `test_install_runs_nothing_in_the_vm_the_second_time(box, tmp_path)`.** Install the same host file twice. Assert the second call adds no entry to `box.raw.calls`, and that `box.installed == {str(script)}`. The existing `..._executable_once` test can absorb this instead, by recording the call count between the two calls. Either is fine; don't keep two tests that check the same thing.
- **(c) `test_a_folder_root_cannot_make_stops_the_install_and_records_nothing(box, tmp_path)`.**
  1. Make the host file at `tmp_path / "host" / "blocked" / "sub" / "f.sh"`.
  2. In the fake VM, make a regular file where the folder's parent would be: `Path(box.fake._map(str(tmp_path / "host" / "blocked")))`, created with its parent folders, then `.write_text("x")`. Now `mkdir -p` fails in the root command.
  3. Assert `box.install(script)` raises `SandboxFailed`, imported from `adw_modules.sandbox`.
  4. Assert `box.installed == set()`, and that no call after the failing one contains `cat >`, so the write was never attempted.
- **E2B only.** `test_e2b_root_commands_run_as_root_not_through_sudo` counts root calls made during `open()`, before any install, so it still sees one. Leave it alone. If it fails, the change has touched `open()`, which the Bar forbids.

Existing tests stay unchanged, apart from the helper edits above.

### 3. Docs

- `docs/designs/remote-sandboxes.md`, section "As built: E2B": add one bullet after "Commands are shell strings.":

  > **SSSF's own files go in through root.** The refuse file an agent call needs (`sssf_refuse.py` or `sssf_rules.ts`) is copied to its host path, in the launch checkout's `adws/adw_modules/`, outside the clone. Root owns the parents `_clone` made, so the VM's user can't make that folder. Root makes it and hands it to the user, who writes the file. Decision 27's proof found this on 2026-11-05, in runs 516d1ffd and 0fd75f1c; earlier E2B proofs had no refuse rules.

  Add no Log row: the Log records live runs, and the fix's live run is the engineer's.
- `.claude/skills/sssf/references/config.md:396` says "In a VM, the runner copies the script in at the host path." It stays true. Don't edit it.
- No CHANGELOG exists in the repo or the skill, and no README or help text describes `install`. The method's docstring is updated in step 1.
- `app_docs/` doesn't exist in this repo. No shipped write-up describes the old `install`.

## Verification

1. **New and changed tests**, from the repo root (the plan's `checks` entry):
   `cd .claude/skills/sssf && env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_remote.py -q -p no:cacheprovider`
   - Every test passes on both the `vercel` and `e2b` params.
   - The `env -u` drops two variables an SSSF agent's shell has set. With them set, `test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules` fails at the baseline too, because it copies `os.environ`.
2. **The new tests catch the old code.** Temporarily restore the baseline `install` body (one `_must` with `mkdir -p "$(dirname "$0")"` as the user). Then test (a) must fail on both params: two calls are expected and one is made. Restore the new body afterwards, and run step 1 again.
3. **The skill's whole suite still passes:**
   `cd .claude/skills/sssf && env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
4. **The repo's own quality checks pass.**
5. **Only the planned files changed:** `git diff --stat` lists exactly these three:
   - `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`
   - `.claude/skills/sssf/tests/test_sandbox_remote.py`
   - `docs/designs/remote-sandboxes.md`

## For the engineer

These steps touch protected paths or need a real VM, so they're not the builder's:

- **Stamp the fix into the hub.** Copy the template's `sandbox_remote.py` to `adws/adw_modules/sandbox_remote.py`, and update `adws/adw_sssf_config/stamp.json`, as in 30249cd and 0d127f8.
- **Prove it on real VMs.** In sssf-sandbox, which has refuse rules, run one Claude Code agent call and one pi agent call under `--sandbox e2b`. Each should start, and a command matching a refuse rule should be refused. Then run the same under `--sandbox vercel`, to confirm it still works there. Add a Log row to `docs/designs/remote-sandboxes.md` with the run IDs, and finish decision 27's proof.
