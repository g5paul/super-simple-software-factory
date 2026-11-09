# Draft upstream report for canyonroad/agentsh

For the engineer to send. Patch: `fix/ptrace-exec-fail-closed` in the fork, commit 31b5af0.

---

**Title:** ptrace: execve is allowed when the tracer can't read its filename or argv

**What happens.** In ptrace mode, `handleExecve` calls `allowSyscall` when `readString` fails on the filename, or `readArgv` fails on argv (`internal/ptrace/tracer.go:2011-2014` and `:2024-2028` at a07c789). The exec then runs with no policy check. `handleFile` denies in the same case (`internal/ptrace/handle_file.go:226-230`), so the two handlers disagree.

**How we hit it.** We run `agentsh server` as a systemd service under its own user, with `CAP_SYS_PTRACE`, and run agents as the engineer's user through `agentsh wrap`. We need this setup on WSL2, because WSL's init holds a seccomp listener on PID 1, so seccomp user-notify fails with `EBUSY` for every process. The wrap attached, but every exec in the policy ran: `just test live`, `gh`, `git push`. The server log showed `handleExecve: cannot read filename ... open /proc/<pid>/mem: permission denied` for each one.

The cause is `/proc/<pid>/mem`. It is mode 0600 and owned by the tracee, and the kernel checks file permissions before ptrace access, so a tracer of another user can't open it, even with `CAP_SYS_PTRACE`. `getMemReader` returns that error from `ensureMemFD` before it tries `process_vm_readv`, which checks only ptrace access and would have worked (`internal/ptrace/memory.go:107-112`, `:136-141`).

**The patch.**

1. `handleExecve` denies with `EACCES` when it can't read the filename or argv.
2. `getMemReader` returns a `vmReader` with no `/proc` fallback when `/proc/<tid>/mem` won't open, so reads use `process_vm_readv` alone.

**Tests.**

- Unit: `vmReader` reads this process's memory with no `/proc` fd, and returns the `process_vm_readv` error, not `EBADF`, on a bad address.
- Integration, `TestIntegration_ExecveUnreadableArgsDenied`: the helper execs with an unreadable filename or argv pointer after the tracer attaches. Before the patch the tracer allows it and the kernel returns `EFAULT`. After it the tracer denies with `EACCES`, and the exec handler isn't called. The rest of the ptrace integration suite gives the same results with and without the patch.

**Also worth a look.**

- `handleSyscallStop` and `handleSeccompStop` allow the syscall when `buildSyscallContext` fails (`tracer.go:1168-1172`, `:1228-1232`). That usually means the tracee is gone, but in seccomp-stop mode every stop is a syscall the policy asked to see.
- `on_attach_failure: fail_closed` still let one wrapped command run before the failed attach stopped the session.
- `max_hold_ms` allows a held syscall after 5 seconds by default.
- `wrap` mounted FUSE over the workspace with `sandbox.fuse.enabled: false`.
- The integration tests in `internal/ptrace` don't run in CI, and several pass when the attach fails, because they only log "may not have been observed". `TestIntegration_ExecveatATEmptyPath` passed in a rootless podman container where every attach failed with `wait4 after interrupt ... timed out`.
- The WSL2 guide promises the full stack, but on WSL 3.0.3.0 with `systemd=true`, `agentsh detect` reports seccomp-notify as `EBUSY`.
