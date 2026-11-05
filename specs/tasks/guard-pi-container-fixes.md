# Close the pi-in-container review's broken promises

Run 28541d50 built decisions 8 to 16 of `docs/designs/container-operator.md`
on branch `guard-pi-container`. Its bypass review
(`adws/adw_data/sessions/28541d50/context_handoff/guard_report.md`) found
three broken promises and four new known limits. This change, on top of that
branch, closes them. Everything else that branch does stays as it is.

What should change:

1. **The stored-login check reads only pi's real path.** `agent_pi`'s
   `PI_AUTH_JSON` honours a `PI_AUTH_PATH` override that `.env` can set, and an
   agent can write `.env`, since gitignored files aren't rolled back. A line
   there points the check at a missing file and a stored `openai` login wins
   over the broker unchecked. Drop the override. The path is pi's own
   `~/.pi/agent/auth.json`, or `PI_CODING_AGENT_DIR`'s `auth.json` when the
   operator's environment sets that, since pi itself reads from there. Tests
   monkeypatch the module value.
2. **The check never blocks.** A FIFO in place of `auth.json` makes its read
   block forever, before pi starts. The check reads the file only when it is a
   regular file, without following a symlink to something else, and treats
   anything else at that path (FIFO, socket, device, directory) as a stop
   naming the path. A missing file passes, as now.
3. **`run.sh` waits for the broker to answer, not for its file.** A socket
   left by an earlier session makes the file test pass even when the new
   broker died. `run.sh` removes any socket at that path before starting the
   broker, then waits until a connection to it succeeds, and exits 1 with a
   message if it doesn't within its wait.

`references/config.md` gains these known limits, next to the one on minting
tokens:

- The stored-login check looks for an `openai` entry only. Other stored
  logins pi keeps are not checked.
- The check and pi read `auth.json` at different moments, so a file swapped
  between them can pass the check and still reach pi.
- An extension path inside the repo that is a symlink to a file outside it
  passes the launch check, and imports aren't followed.
- Each connection to the socket starts one host `pi` process, with no cap, so
  a process in the container can load the host.
- `--sandbox vercel|e2b` launched from inside the container mints its VM
  tokens with the container's own pi, not through the broker.

A host run, a VM run, and a Claude Code roster in the container keep exactly
today's behaviour.
