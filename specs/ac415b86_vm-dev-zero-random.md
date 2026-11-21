# Let a VM's processes read /dev/zero and /dev/random

## Summary

In an OpenShell VM nothing can open `/dev/zero`, so `head -c N /dev/zero` and `dd if=/dev/zero` fail there and work on the host and under the local agentsh guard. This change adds `/dev/zero` and `/dev/random` to the VM policy's `read_only` list, and nothing else.

**The std streams are not fixed by this change.** The request asked for a fix to `/dev/stdin`, `/dev/stdout` and `/dev/stderr` only if it gives a process nothing past its own descriptors. That could not be shown from the repo, and a wrong guess could stop every VM from starting (see "The std streams" below). The plan records the limit in the docs and gives the engineer the three commands that settle it.

## Baseline

`main` at `57e45b5` ("Add guard brief for letting a VM open /dev/zero and the std streams"). Every finding is judged against it.

At the baseline, `policy()` in `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py` writes:

- `read_only`: `/bin`, `/usr`, `/lib`, `/proc`, `/dev/urandom`, `/etc`, `/var/log` (`READ_ONLY_PATHS`, line 65)
- `read_write`: `/tmp`, `/var/tmp`, `/dev/null`, the baked folder (line 198)
- `include_workdir: true`, `landlock: {compatibility: hard_requirement}`

`DEVICES` in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` (line 139) lists `/dev/null`, `/dev/zero`, `/dev/tty`, `/dev/ptmx`, `/dev/pts/**`, `/dev/urandom`, `/dev/random`, `/dev/fd/**`, `/dev/stdin`, `/dev/stdout`, `/dev/stderr`.

## Each device, and the decision

The facts are the engineer's VM table of 2026-11-21 in the request. No VM was started for this plan.

| Device | In the VM today | Decision | Why |
|---|---|---|---|
| `/dev/zero` | denied | **add, `read_only`** | Reading gives zeros and nothing else. `head -c N /dev/zero` and `dd if=/dev/zero` are common. |
| `/dev/random` | denied | **add, `read_only`** | Gives random bytes, as `/dev/urandom` already does. gpg, Java's default `SecureRandom` source and some OpenSSL builds open it by name. |
| `/dev/null` | read and write ok | no change | Already in `read_write`. |
| `/dev/urandom` | read ok | no change | Already in `read_only`. |
| `/dev/fd/**` | `/dev/fd/0` read and write ok | no change | Works already. |
| `/dev/tty` | denied | **leave out** | An exec has no controlling terminal (`--no-tty`, and `tty` says "not a tty"), so opening `/dev/tty` fails with or without a rule. A rule would change nothing. |
| `/dev/ptmx`, `/dev/pts/**` | denied | **leave out** | They let a process make terminals, which only `script`, `expect`, `tmux` and the like need. A rule on `/dev/pts` covers every terminal in the VM, and all execs run as the same user (`ubuntu`), so a process could read and type into a terminal another session holds, such as an engineer's `openshell sandbox connect`. That is past its own descriptors. |
| `/dev/stdin`, `/dev/stdout`, `/dev/stderr` | denied | **leave out, record as open** | See the next section. |
| `/dev/full` | denied | leave out | Not in `DEVICES`, so outside the request. |

### `/dev/zero`: `read_only`, not `read_write`

A write to `/dev/zero` is discarded, as with `/dev/null`, so `read_write` would give a process nothing harmful. But no common command writes to it, `/dev/null` is already there for discarding output, and `read_only` is the smaller grant. It also matches `/dev/urandom`. The cost: a program that opens `/dev/zero` read-write (the old `mmap` idiom for shared memory) still fails in a VM. That is rare. If one turns up, moving the path is a one-line change.

### That both paths exist in the VM

Design doc `docs/designs/openshell-sandbox.md` says Landlock applies a rule only to a path that exists when the sandbox starts, and that under `hard_requirement` a sandbox whose file rules can't apply doesn't start. So a path that is missing in the VM could stop every VM run. `/dev/zero` exists: the engineer's `head` got "Permission denied", not "No such file". `/dev/random` is made by the same kernel driver as `/dev/zero`, `/dev/null` and `/dev/urandom`, and by the same default list in container runtimes, so it is there when those are. The engineer's proof after the merge is the check of record.

## The std streams

**What the facts rule out.** The request's guess is that `/dev/stdout` is a link to `/proc/self/fd/1` and is refused because `/proc` is in `read_only`. The table doesn't fit that:

- Landlock judges an open by the file it ends at, never by the name or the links on the way. On the usual Linux layout `/dev/stdin` is a link to `/proc/self/fd/0`, and `/dev/fd` is a link to `/proc/self/fd`, so `/dev/stdin` and `/dev/fd/0` end at the same file and get the same answer.
- In the VM `/dev/fd/0` opens for reading and for writing, and `/dev/stdin` is denied for both.
- The write through `/dev/fd/0` succeeded, and it went through `/proc/self/fd`. So `/proc` being read-only isn't what refuses a write there.

So one of these holds in the VM, and the repo can't say which: `/dev/stdin`, `/dev/stdout` and `/dev/stderr` are not links to `/proc/self/fd/N` there (missing, or something else), or OpenShell refuses them by some means beyond Landlock's file rules.

**What was not checked.** The `openshell` binaries are not readable under agentsh (`ls` of their folder was denied), so how OpenShell turns a policy path that is a link into a rule wasn't read. Listing `/dev` is denied in the VM, and no VM was started.

**Why no fix is planned.** Adding the three paths to `read_write` blind has two risks no host test sees:

1. If they are links, the supervisor most likely opens each path to make its rule, and the link then leads to the supervisor's own descriptor, not the process's. That grants a file past the process's own descriptors, which the request rules out.
2. If they don't exist in the VM, the rule can't apply, and under `hard_requirement` every VM run stops at `sandbox create`.

**What works today.** `echo msg >&2` and `>&1` copy a descriptor the process already holds and open nothing, so they work in a VM. `/dev/fd/N` works too. The docs step below says so.

## Threat model

The policy is the file rules for every process in a run's VM: the agents, the checks and git, all as the sandbox user `ubuntu`. An agent there runs any command it likes.

- `/dev/zero` gives a process an endless run of zero bytes. It can already make one (`yes`, a loop), and already fill `/tmp`, `/var/tmp` and the baked folder. The VM's size caps that, not this policy.
- `/dev/random` gives the same bytes as `/dev/urandom`, which is already readable.
- Both are `read_only`, so nothing new can be written. Neither reaches the host, another VM, another process's memory or a file.

What this guard can't stop at its level: on the host the builder runs as the engineer's own user and edits the guard code itself. It could widen the policy past this spec. The tests below compare every other part of the policy with the baseline, value for value, so a wider policy fails them, and the reviewer reads the diff. A policy is read by OpenShell, which SSSF doesn't control; how OpenShell enforces a rule is taken from the design doc and the engineer's table, not proven here.

## Bar

Against the baseline, the change must:

- Put `/dev/zero` and `/dev/random` in `read_only` for every roster (`claude_code`, `pi`, both).

It must never:

- Put either in `read_write`.
- Add, drop or reorder any other path in `read_only` or `read_write`.
- Change the network rules, `include_workdir`, the `landlock` setting or the image (`containerfile`, `image_tag`), for any roster.
- Keep an agent's file, run an agent's code, or delete or overwrite an engineer's file where the baseline doesn't. The change touches one list constant and adds no file write, so none of these moves.

Every existing test passes on the host.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

- Line 65, change `READ_ONLY_PATHS` to:

  ```python
  READ_ONLY_PATHS = ["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/dev/zero", "/dev/random",
                     "/etc", "/var/log"]
  ```

- Add a short comment above it: the devices are read-only because nothing needs to write them; `/dev/tty`, `/dev/ptmx`, `/dev/pts` and the three std streams are left out on purpose, and `>&2` works without them.
- In the module docstring, after the `TMPDIR` paragraph, add one or two sentences: which devices a VM's processes can open (`/dev/null` read-write; `/dev/urandom`, `/dev/zero`, `/dev/random` read-only; `/dev/fd/N`), and that `/dev/stdout` and `/dev/stderr` don't open, so use `>&2`.
- Change nothing else. `policy()` already copies the list.

### 2. `.claude/skills/sssf/tests/test_sandbox_openshell.py`

Update two existing asserts to the new list:

- `test_the_default_policy` (line 205): the `read_only` literal.
- `test_the_image_holds_rsync_and_nothing_else_changed` (line 381): the `READ_ONLY_PATHS` literal.

Add these tests near `test_the_default_policy`. Write the baseline lists as literals in the test, never read from `osh`. Build rosters with the existing `roster()` helper and `KINDS`, which load the shipped `templates/sssf.config.yaml`.

- **The devices** (`test_a_vm_can_read_dev_zero_and_dev_random`), over the three `KINDS`: `/dev/zero` and `/dev/random` are in `filesystem_policy["read_only"]` and not in `read_write`.
- **Only what the spec names** (`test_the_policy_differs_from_the_baseline_only_by_the_two_devices`), over the three `KINDS`, with `p = osh.policy(F, [], sandbox_remote.harnesses(roster(*KINDS[kind])))`:
  - `read_only` with the two devices removed equals `["/bin", "/usr", "/lib", "/proc", "/dev/urandom", "/etc", "/var/log"]`, in that order, and `read_only` holds no path twice.
  - `read_write == ["/tmp", "/var/tmp", "/dev/null", str(F)]`.
  - `include_workdir is True` and `p["landlock"] == {"compatibility": "hard_requirement"}`.
  - `set(p) == {"version", "filesystem_policy", "landlock", "network_policies"}`, `set(p["filesystem_policy"]) == {"include_workdir", "read_only", "read_write"}` and `p["version"] == 1`.
  - `network_policies` equals a dict built from literals: `expected_rules()["anthropic_api"]` when the roster has a Claude Code agent, the `OPENAI` constant when it has a pi agent, and `expected_rules()["package_registries"]`.
  - None of `/dev/tty`, `/dev/ptmx`, `/dev/pts`, `/dev/stdin`, `/dev/stdout`, `/dev/stderr`, `/dev/full` is in either list.
- **The image** : in the same test or one more, `"/dev/zero" not in osh.containerfile(F, "")` and `"/dev/random" not in osh.containerfile(F, "")`, so the image tag doesn't move.
- **What a VM gets** (`test_create_writes_the_devices_into_the_policy_file`): copy the shape of `test_tmpdir_is_in_the_policy_and_outside_the_repo` (the `cli`, `layout` and `write_shipped_config` helpers), call `_create()`, load `openshell-policy.yaml` and assert both devices are in `read_only` and not in `read_write`.

### 3. `.claude/skills/sssf/references/config.md`

The OpenShell section ("A local sandbox: `--sandbox openshell`", line 725 on) doesn't list the policy's paths today. Add one short paragraph after the network paragraph (line 733), starting `**Devices.**`: a VM's processes can read and write `/dev/null`, read `/dev/urandom`, `/dev/zero` and `/dev/random`, and open `/dev/fd/N`. `/dev/tty`, `/dev/ptmx` and `/dev/pts` are closed, since agents run with no terminal. `/dev/stdin`, `/dev/stdout` and `/dev/stderr` don't open either, so a script writes `echo msg >&2`, not `echo msg > /dev/stderr`. A policy change doesn't change the image, so no `just openshell-setup` is needed.

### 4. `docs/open.md`

In the row that starts "The hub's suite still fails in an OpenShell VM":

- Delete cause (2), from `(2) **\`test_sandbox_remote.py\`'s "a line with no end in the file is skipped"` through `Cause not found.`
- Renumber cause (3), "Five tests that need a closer look", to (2).
- Change "Run 4a74c2b9 never reached the file behind cause (2), and reached only one of cause (1)'s." to "Run 4a74c2b9 reached only one of cause (1)'s."
- After the sentence about the 3 tests fixed by run 9f8417b9, add: "The 2 `test_sandbox_remote.py` tests ("a line with no end in the file is skipped", vercel and e2b) failed because nothing in a VM could open `/dev/zero`; run ac415b86 added it to the VM's policy, not yet rerun in a VM."
- Leave the run history and its counts as they are.

Add one new row, in its place in the sort (Bug, P3, Low, Ready, since 2026-11-21, background `[openshell-sandbox.md](designs/openshell-sandbox.md)`): "`/dev/stdin`, `/dev/stdout` and `/dev/stderr` don't open in an OpenShell VM, so `echo msg > /dev/stderr` fails there; `>&2` works. `/dev/fd/0` opens for reading and writing in the same VM, so the cause isn't `/proc` being read-only: the three are either not links to `/proc/self/fd/N` in the VM or refused by something other than Landlock's file rules. Not fixed by run ac415b86, because a policy path that is a link may grant the supervisor's descriptor, and one that doesn't exist stops the VM from starting. In a VM, `readlink /dev/stdin /dev/stdout /dev/stderr /dev/fd` and `ls -l /proc/self/fd` settle it."

### Docs that stay as they are

- `docs/research/pi-under-openshell.md` lists the old paths. It is a dated research note with line references to an old commit. Leave it.
- `docs/designs/openshell-sandbox.md` decision 8 says "the default read-only system paths" and names no device. Still true.
- No write-up under `app_docs/` names the policy's paths, so the documenter has nothing old to note.
- The repo keeps no CHANGELOG.

## Verification

From `.claude/skills/sssf`, the way the test file's own header says:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_sandbox_openshell.py tests/test_sandbox_remote.py -m 'not pushes and not host_only'
```

Exit 0 means pass. Run only these two files; a whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). The whole suite is verify's job.

**Known problem under agentsh.** The planner ran this command on the baseline and it exited 2 at collection: importing `adw_modules/utils.py` calls `load_dotenv()`, which walks up from the worktree to the main checkout's `.env`, and agentsh denies that read (`PermissionError: ... /home/gpaul/github/super-simple-software-factory/.env`). So the planner did not see these tests pass or fail. If the builder gets the same error, it must not work around it (no `.env` planted in the worktree, no changed lookup): say so in its report and leave the run to verify on the host.

The new tests must fail on the baseline code and pass after step 1. The builder shows that by running them before step 1 if the command collects.

## Out of scope

Holes and limits this change leaves open; a reviewer files them as old:

- `/dev/stdin`, `/dev/stdout` and `/dev/stderr` still don't open in a VM. New row in `docs/open.md`.
- `/dev/tty`, `/dev/ptmx`, `/dev/pts/**` and `/dev/full` stay closed in a VM. A command that needs a terminal still fails there.
- A program that opens `/dev/zero` or `/dev/random` for writing still fails in a VM.
- The local agentsh policy's device list (`DEVICES`) is untouched, so the two guards still differ on terminals and std streams.
- The remote VM modes (`vercel`, `e2b`) have no such policy and are untouched.
- The other VM-only failures in the same `open.md` row.
- A VM process can fill its own disk and memory, as on the baseline.
- `/proc` stays readable in the VM, as on the baseline.

## For the engineer

- **The hub's own install.** `adws/adw_modules/sandbox_openshell.py` is the hub's installed copy and is protected. It keeps the old list until you update the install after the merge.
- **The proof, after the merge.** In a throwaway VM on the new policy: `tests/test_sandbox_remote.py::test_a_line_with_no_end_in_the_file_is_skipped` passes for vercel and e2b; `head -c 1 /dev/zero` and `head -c 1 /dev/random` exit 0; `printf x > /dev/zero` and `printf x > /dev/random` are denied. The VM starting at all proves both paths exist there.
- **The std streams.** In the same VM, run `readlink /dev/stdin /dev/stdout /dev/stderr /dev/fd`, `ls -l /proc/self/fd`, `head -c 1 /dev/stdin; echo $?` and `echo x > /dev/fd/2`. If the three are missing, nothing in the policy can fix it and the image or OpenShell has to make them. If they are links to `/proc/self/fd/N` and still refused, OpenShell is refusing by name, and its source or its maintainers say how a policy path that is a link is resolved before it is safe to list one.
- **The tests under agentsh.** The two test files can't be collected under agentsh from a worktree, because `utils.py`'s `load_dotenv()` reaches the main checkout's `.env`. If the builder reports the same, the brief's "run only these two files" step doesn't work as written.
