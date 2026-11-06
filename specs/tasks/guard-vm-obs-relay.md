# Ask for the obs relay from a pi agent's VM env, and turn obs off with the value the extension reads

Decisions 37 to 50 of `docs/designs/remote-sandboxes.md` add an obs relay for
pi agents in a VM. The relay, the host forwarder and the VM wiring are built
on this branch, outside the guard files: `adw_modules/obs_relay.py`,
`adw_modules/sssf_obs_relay.cjs`, and `Remote.relay_obs` and `Remote.start`
in `adw_modules/sandbox_remote.py`. `Remote.relay_obs(target)` asks for a
relay for the next agent that `Remote.start` starts, and only that one. When
the relay comes up, `start` points the agent's `OBS_SERVER_URL` at the
relay's loopback address and removes `OBS_DISABLE` from the agent's env. If
the relay doesn't come up, the agent's env is left as it was given.

What's left is `agent_pi._in_vm`, a guard file's code. Today it sets
`OBS_DISABLE=1` and drops `OBS_AUTH_TOKEN` for every pi call in a VM. Two
problems:
- Nothing asks for a relay.
- The pi-observability extension treats only `OBS_DISABLE=true` as off
  (`process.env.OBS_DISABLE === "true"` in pi-agent-observability's
  `extension/pi-observability.ts`). So `1` has left obs on in every VM run,
  posting without a token to an address in the VM where nothing listened.

What should change, on a remote tree only:
- `_in_vm` still drops `OBS_AUTH_TOKEN` from the env the VM gets. The token
  goes to the host-side relay request (`obs_relay.Target.token`) and nowhere
  else. No value of it reaches any env, argv, file or trace event the VM or
  the agent sees.
- The agent's env gets `OBS_DISABLE=true`, the value the extension reads, in
  place of `1`.
- `_in_vm` asks the tree for a relay, through `tree.relay_obs(obs_relay.Target(...))`,
  only when all of these hold:
  - the call loads at least one extension from the roster
    (`request.extensions`), not counting SSSF's refuse extension;
  - the host env had a non-empty `OBS_AUTH_TOKEN`;
  - the operator's own `OBS_DISABLE` wasn't `true`.
- The Target's fields come from the env `child_env` built for the call:
  - `server`: `OBS_SERVER_URL`, or `obs_relay.DEFAULT_SERVER` when unset or
    empty;
  - `name`: `OBS_NAME`;
  - `pool`: `OBS_POOL`;
  - `tags`: `OBS_TAG` split on commas, trimmed, with empty entries dropped.
- `run` passes the call's extensions to `_in_vm`.

These stay as they are today:
- A host run and an operator-container run (`sandbox.LOCAL`).
- A Claude Code roster.
- `Remote._env`'s `HELD` rule.
- The refuse extension loading last.
- The ChatGPT token's path into the VM.

Tests:
- Pin each condition for asking or not asking.
- Pin the Target's fields.
- Show that the token is in no env the VM gets.
- Show that `OBS_DISABLE=true` is set.
- Update the existing tests that expect `OBS_DISABLE` to be `1`.

`references/config.md` says, in a VM:
- the agent's obs events go through the relay to the host's obs server when
  the roster loads an extension and the host has `OBS_AUTH_TOKEN`;
- otherwise obs is off.
