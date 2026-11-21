# Let a VM's processes open /dev/zero and the std streams

Run it with `--worktree` and `--base main`, on the hub's own config. The change is the OpenShell VM's filesystem policy in `sandbox_openshell.py`, which is guard code, so it goes through the chain.

## What broke

In an OpenShell VM, nothing can open `/dev/zero`: `head -c 10 /dev/zero` exits 1 with `head: cannot open '/dev/zero' for reading: Permission denied`. The policy `policy()` writes lists devices in two places, `/dev/urandom` in `READ_ONLY_PATHS` and `/dev/null` in `read_write`, and no other device.

Two skill tests fail in a VM because of it and pass on the host: `tests/test_sandbox_remote.py::test_a_line_with_no_end_in_the_file_is_skipped`, its vercel and e2b cases. The test builds an 8 MB line with `head -c 8000000 /dev/zero | tr "\0" a`. In the VM the `head` fails, only the empty line from `echo` lands in the relay's file, and the relay has nothing to drop, so `dropped` is 0 where the test wants 1. The engineer checked on 2026-11-21: with the line made from `yes a | tr -d "\n" | head -c 8000000` instead, and nothing else changed, both cases pass in the VM.

The local agentsh policy lets agents open `/dev/zero`: `DEVICES` in `agentsh_guard.py` lists `/dev/null`, `/dev/zero`, `/dev/tty`, `/dev/ptmx`, `/dev/pts/**`, `/dev/urandom`, `/dev/random`, `/dev/fd/**` and the three std streams. So a command that reads `/dev/zero` works under the local guard and fails in a VM, and a project's own suite that uses `dd if=/dev/zero` or the same `head` idiom fails only in a VM.

## The change

1. **`/dev/zero` read-only.** Add it to `READ_ONLY_PATHS`. Reading it gives a process zeros and nothing else. Say in the spec whether it belongs in `read_only` or `read_write` (writes to `/dev/zero` are discarded, as with `/dev/null`; pick one and say why).
2. **The rest of agentsh's devices.** For each other device in `DEVICES` that the VM's policy doesn't list, say in the spec whether a VM needs it and what it would let a process reach. Add only the ones that give a process nothing past its own VM and that a common command needs. `/dev/tty`, `/dev/ptmx` and `/dev/pts/**` need a reason, since agents run with no terminal.

   The engineer checked a VM on 2026-11-21, with each device opened for reading (`head -c 1`) and for writing (`printf x >`) from a `sandbox exec` with no terminal:

   | Device | Read | Write |
   |---|---|---|
   | `/dev/null` | ok | ok |
   | `/dev/urandom` | ok | denied |
   | `/dev/fd/0` | ok | ok |
   | `/dev/zero`, `/dev/random`, `/dev/full` | denied | denied |
   | `/dev/tty`, `/dev/ptmx` | denied | denied |
   | `/dev/stdin`, `/dev/stdout`, `/dev/stderr` | denied | denied |

   Listing `/dev` and `/dev/pts` is denied too, and `tty` says "not a tty". `/dev/stdout` and `/dev/stderr` matter most: `echo msg > /dev/stderr` is a common shell idiom, and it fails in a VM. Find out why they're refused while `/dev/fd/0` works (on Linux they are usually links to `/proc/self/fd/N`, and `/proc` is in `READ_ONLY_PATHS`), say it in the spec, and fix it if the fix gives a process nothing past its own descriptors. The tests start no real VM, so the spec rests on these facts and on what the code shows.
3. **Docs.** The OpenShell section of `references/config.md` says which devices a VM's processes can open, if it lists the policy's paths. In `docs/open.md`'s row that starts "The hub's suite still fails in an OpenShell VM", drop cause (2), the `test_sandbox_remote.py` tests, and renumber the rest. A policy change changes nothing about the image, so `image_tag` stays.

## The bar

- **The devices.** A test on `policy()` sees `/dev/zero`, and each other path the spec adds, in the list the spec picked for it.
- **Only what the spec names.** Every other path in `read_only` and `read_write`, the network rules, `include_workdir` and the landlock setting are as on `main`, for each roster (`claude_code`, `pi`, both). A test compares them.
- **Nothing else changes.** Every existing test passes on the host.

## After the merge, the engineer's proof

Not part of the run, since no test starts a real VM: update the hub's own install, then run `tests/test_sandbox_remote.py::test_a_line_with_no_end_in_the_file_is_skipped` in a throwaway VM and see both cases pass. In the same VM, each device the spec adds opens as the spec says, and `echo x > /dev/stderr` works if the spec fixes it.

## Running the tests

The planner and the builder run under agentsh. Run only `tests/test_sandbox_openshell.py` and `tests/test_sandbox_remote.py` from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`).

## Out of scope

- The other VM-only failures in the same `open.md` row.
- The local agentsh policy's device list.
- The remote VM modes (`vercel`, `e2b`).
