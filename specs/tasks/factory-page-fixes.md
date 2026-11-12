Fix two bugs in the factory page that run e089a1ee built (commit 4ec7c30 on this branch), found by reading its JSON API against the hub's real traces.

1. The live-VM rows count events that aren't VMs. In the trace, `sandbox` names both the VM's start event (type `log`, with `vm`, `provider` and `session_minutes` in its payload) and the `sandbox` phase's own events (`phase_start`, `phase_end`, and `log` lines with only `message` and `level`). `SssfDb.openSandboxes` in `.claude/skills/sssf/apps/visualizer/server/db.ts` takes every `sandbox` event, so each VM run adds about five "VM (unnamed)" rows that never end: on 2026-11-11 the board showed about 60 live VMs and 300 amber rows, where there were none. Count only a VM's start event.
2. The agentsh store row's next step says "ask the hub session to check why the prune isn't shrinking the store". The store file never shrinks; the step is the one `.claude/skills/sssf/references/config.md` gives under "Running local agents under agentsh": with no run going, stop the service, move `/var/lib/agentsh/events.db` and its `-wal` and `-shm` files aside, start it, and delete them once a run works.

Where: `.claude/skills/sssf/apps/visualizer/server/` (`db.ts`, the factory modules and their tests).

Done means: a test seeds a trace the way a real VM run writes it, the `sandbox` phase's events beside the VM's start and end, and gets no live VM; one with a start and no end still gets one. The store row's next step matches config.md. `bun test` and `npx --no-install vue-tsc --noEmit` pass in the dashboard's folder.

In your shell, agentsh denies a connect to `127.0.0.1`, so `server/listen.test.ts` fails there with "Permission denied"; the chain's test step runs it outside your shell. Test through the server's functions, not over a socket.

Out of scope: every other row and rule, the design's "Deferred" list, `check_projects.py`, `phase_times.py`, guard code, the agentsh policy, `quality.py` and the docs.
