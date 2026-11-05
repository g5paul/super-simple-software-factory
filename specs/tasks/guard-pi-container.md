# Let a pi roster run in the operator container

Build decisions 8 to 16 of `docs/designs/container-operator.md`, settled with
the engineer on 2026-11-05. Read them first; they are the bar's source.
Decision 2 there now points at them.

Today `install.py --container` refuses a pi roster ("--container needs a
Claude Code roster: the image has no Pi"), the image (`templates/container/
Containerfile`) has no node or pi, and only the Claude token goes in through
`run.sh`. A VM run already signs pi in with a one-hour ChatGPT access token
from the host (`agent_pi.vm_token`, `agent_pi._in_vm`), passed as
`OPENAI_API_KEY`.

What should change:

- `container/pi_token_broker.py`, stamped with the other container files: a
  stdlib Python script the host runs. It listens on a Unix socket, mode 600,
  and for each connection runs `pi auth print-bearer-token --provider openai
  --min-expiry 35m` and writes back the token or one error line. It never logs
  or prints the token. It refuses a credential that starts with `sk-`.
- `run.sh` starts the broker in the background before `podman run`, on
  `$XDG_RUNTIME_DIR/sssf-pi-<repo>.sock`, stops it with a `trap` on exit,
  mounts the socket at `/run/sssf/pi.sock`, and sets `SSSF_PI_TOKEN_SOCKET` to
  that path. It forwards the container's `127.0.0.1:43190` to the host's with
  pasta's `-T 43190`. It rebuilds the image when the image's label for the
  Containerfile's hash doesn't match, and `build.sh` sets that label.
- The Containerfile installs node (major 24) and pi at an exact pinned
  version, 1.4.0.
- When `SSSF_PI_TOKEN_SOCKET` is set, each pi agent call gets its token from
  the socket as `OPENAI_API_KEY`, the way a VM run does. A broker error stops
  that agent call before pi starts, with the broker's message. Before the
  first pi agent, a ChatGPT login stored in the container's
  `~/.pi/agent/auth.json` (an `openai` entry, checked by key name only) stops
  the run and says to remove it.
- Before the run starts in the container, a `harness_engineering` path outside
  the repo stops it with a message naming the agent and the path.
  Uncommitted paths inside the repo are fine.
- `install.py --container` accepts a pi roster.
- `cookbooks/install.md` says a pi roster in the container needs the host
  signed in to pi. `references/config.md` lists the known limit from decision
  9: any process in the container can get a one-hour token from the socket.

A host run, a VM run, and a Claude Code roster in the container keep exactly
today's behaviour. In the container obs works as on the host: no
`OBS_DISABLE`, and `OBS_AUTH_TOKEN` is not held back (decision 13). The tests
need no Podman and no real pi sign-in; the proof in a real container runs
after the merge, by hand (decision 16).
